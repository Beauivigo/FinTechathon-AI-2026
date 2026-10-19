from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import adapt_request, adapt_result, ServiceError


class MerchantTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'bank.db');initialize(self.store)
        self.now=datetime(2026,10,19,tzinfo=timezone.utc)
        self.g=BankGateway(self.store,clock=lambda:self.now)

    def req(self,action,params,key=None):
        return adapt_request(action,params,request_id=uuid4().hex,trace_id=uuid4().hex,idempotency_key=key or uuid4().hex)

    def send(self,request): return self.g.execute(request,principal_id='demo_user_a')

    def approve(self,pending,decision='approve'):
        n=pending['next_action'];payload=dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision=decision)
        if decision=='approve' and n['kind']=='verify':
            payload['proof_token']=self.g.issue_demo_proof(n['challenge_id'],principal_id='demo_user_a')
        return self.g.authorize(payload,principal_id='demo_user_a')

    def reserve(self,amount=50000):
        return self.approve(self.send(self.req('budget.reserve',dict(account_id='demo_account_a',amount_minor=amount,purpose='模拟生日'))))['data']['reservation_id']

    def order(self,rid,**overrides):
        return self.req('merchant.order',dict(account_id='demo_account_a',reservation_id=rid,item_id='ITEM-BD-FLOWER-01',amount_minor=19900,deliver_on='2026-11-20',delivery_address_id='ADDR-BD-01')|overrides)

    def release(self,rid): return self.send(self.req('budget.release',dict(reservation_id=rid)))

    def balance(self):
        with self.store.connection() as c:
            return tuple(c.execute("SELECT balance_minor,available_minor,reserved_minor FROM accounts WHERE account_id='demo_account_a'").fetchone())

    def test_atomic_partial_consume_then_release(self):
        rid=self.reserve();req=self.order(rid);pending=self.send(req)
        self.assertEqual(pending['status'],'awaiting_verification');self.assertEqual(self.balance(),(200000,150000,50000))
        done=self.approve(pending);self.assertEqual(done['status'],'succeeded');self.assertEqual(set(done['data']),{'order_id','amount_minor'})
        self.assertEqual(self.balance(),(180100,150000,30100))
        self.assertEqual(self.send(req),done)
        self.approve(self.release(rid));self.assertEqual(self.balance(),(180100,180100,0))
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT state FROM reservations').fetchone()[0],'closed')
            self.assertEqual(c.execute('SELECT count(*) FROM ledger_entries').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT amount_minor,kind,direction FROM ledger_entries').fetchone()[:],(19900,'consumption','debit'))

    def test_full_consume_closes_no_release(self):
        rid=self.reserve(19900);self.approve(self.send(self.order(rid)))
        self.assertEqual(self.balance(),(180100,180100,0));self.assertEqual(self.release(rid)['error']['code'],'INVALID_STATE')

    def test_rejected_order_can_release_and_late_approval_does_not_commit(self):
        rid=self.reserve();p=self.send(self.order(rid));self.approve(p,'reject')
        self.approve(self.release(rid));n=p['next_action']
        late=self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve'),principal_id='demo_user_a')
        self.assertEqual(late['status'],'cancelled')
        self.assertEqual(self.balance(),(200000,200000,0))

    def test_pending_order_amount_is_retained_and_remaining_can_release(self):
        rid=self.reserve();p=self.send(self.order(rid));self.approve(self.release(rid))
        self.assertEqual(self.balance(),(200000,180100,19900))
        self.assertEqual(self.approve(p)['status'],'succeeded');self.assertEqual(self.balance(),(180100,180100,0))

    def test_unresolved_known_amount_and_missing_evidence(self):
        rid=self.reserve();req=self.order(rid);self.send(req)
        with self.store.connection(write=True) as c:
            unknown=adapt_result(req,dict(status='unknown',error=dict(code='RESULT_UNKNOWN',message='模拟未知')),source='sandbox')
            c.execute("UPDATE operations SET result_json=? WHERE action='merchant.order'",(json.dumps(unknown),))
        self.approve(self.release(rid));self.assertEqual(self.balance(),(200000,180100,19900))
        self.assertEqual(self.release(rid)['error']['code'],'INVALID_STATE')
        self.assertEqual(self.send(req)['status'],'unknown')
        with self.store.connection(write=True) as c: c.execute('DELETE FROM merchant_intents')
        self.assertEqual(self.release(rid)['error']['code'],'INVALID_STATE')

    def test_release_confirmation_rechecks_late_order(self):
        rid=self.reserve();release=self.release(rid);order=self.send(self.order(rid));self.approve(order)
        self.assertEqual(self.approve(release)['error']['code'],'AUTH_INVALID')
        self.assertEqual(self.balance(),(180100,150000,30100))

    def test_audit_failure_rolls_back_order_ledger_funds_then_safe_release(self):
        rid=self.reserve();p=self.send(self.order(rid));original=self.g._audit
        def fail(c,r,principal,outcome,now,**details):
            if r['action']=='merchant.order' and outcome=='succeeded': raise ServiceError('UPSTREAM_ERROR','模拟审计失败')
            return original(c,r,principal,outcome,now,**details)
        with patch.object(self.g,'_audit',side_effect=fail): self.assertEqual(self.approve(p)['status'],'failed')
        self.assertEqual(self.balance(),(200000,150000,50000))
        with self.store.connection() as c:
            for table in ('merchant_orders','ledger_entries','fund_postings'): self.assertEqual(c.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        self.approve(self.release(rid));self.assertEqual(self.balance(),(200000,200000,0))

    def test_committed_reply_loss_restart_replay(self):
        rid=self.reserve();req=self.order(rid);p=self.send(req);done=self.approve(p)
        self.g=BankGateway(Store(self.store.path),clock=lambda:self.now)
        self.assertEqual(self.send(req),done)
        query=self.g.execute(adapt_request('operation.get',dict(original_action='merchant.order',original_idempotency_key=req['idempotency_key']),request_id='recover',trace_id='recover'),principal_id='demo_user_a')
        self.assertEqual(query['data']['operation_result'],done);self.assertEqual(self.balance(),(180100,150000,30100))

    def test_card_selection_and_revalidation(self):
        rid=self.reserve()
        with self.store.connection(write=True) as c: c.execute("INSERT INTO cards VALUES ('AAA','demo_account_a','active',50000,0)")
        req=self.order(rid);p=self.send(req)
        with self.store.connection() as c:
            selected=json.loads(c.execute('SELECT resource_binding_json FROM challenges WHERE challenge_id=?',(p['next_action']['challenge_id'],)).fetchone()[0]);self.assertEqual(selected[0],'AAA')
        with self.store.connection(write=True) as c: c.execute("UPDATE cards SET card_state='frozen',revision=revision+1 WHERE card_id='AAA'")
        self.assertEqual(self.approve(p)['error']['code'],'AUTH_INVALID')
        p=self.send(self.order(rid,card_id='CARD-E001'));self.assertEqual(self.approve(p)['status'],'succeeded')

    def test_selection_rejects_missing_inactive_limit_foreign(self):
        rid=self.reserve()
        for card,expected in [('CARD-E002','INVALID_STATE'),('CARD-E003','INVALID_STATE'),('other','FORBIDDEN')]:
            self.assertEqual(self.send(self.order(rid,card_id=card))['error']['code'],expected)
        with self.store.connection(write=True) as c: c.execute("UPDATE cards SET limit_minor=1 WHERE card_state='active'")
        self.assertEqual(self.send(self.order(rid))['error']['code'],'LIMIT_EXCEEDED')
        with self.store.connection(write=True) as c: c.execute("UPDATE cards SET card_state='frozen'")
        self.assertEqual(self.send(self.order(rid))['error']['code'],'INVALID_STATE')
        with self.store.connection(write=True) as c: c.execute('DELETE FROM cards')
        self.assertEqual(self.send(self.order(rid))['error']['code'],'NOT_FOUND')

    def test_input_price_date_account_and_amount_guards(self):
        rid=self.reserve()
        for changes,code in [(dict(amount_minor=1),'INVALID_REQUEST'),(dict(deliver_on='2026-10-19'),'INVALID_REQUEST'),(dict(item_id='missing'),'NOT_FOUND'),(dict(delivery_address_id='missing'),'NOT_FOUND'),(dict(account_id='demo_account_li'),'FORBIDDEN')]:
            self.assertEqual(self.send(self.order(rid,**changes))['error']['code'],code)
        small=self.reserve(100);self.assertEqual(self.send(self.order(small))['error']['code'],'INSUFFICIENT_FUNDS')

    def test_two_pending_orders_cannot_over_allocate(self):
        rid=self.reserve(19900)
        with ThreadPoolExecutor(max_workers=2) as pool: replies=list(pool.map(self.send,[self.order(rid),self.order(rid)]))
        self.assertEqual(sorted(r['status'] for r in replies),['awaiting_verification','failed'])
        p=next(r for r in replies if r['status']=='awaiting_verification');self.approve(p)
        self.assertEqual(self.balance(),(180100,180100,0))

    def test_expired_order_and_cross_user_authorization(self):
        rid=self.reserve();p=self.send(self.order(rid));n=p['next_action']
        with self.assertRaises(ServiceError): self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve'),principal_id='demo_user_li')
        self.now+=timedelta(minutes=11)
        reply=self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve'),principal_id='demo_user_a')
        self.assertEqual(reply['error']['code'],'AUTH_EXPIRED');self.approve(self.release(rid))

    def test_cancelled_merchant_task_releases_and_late_request_is_rejected(self):
        rid=self.reserve();req=self.order(rid)
        reservation=self.g.list_reservations(principal_id='demo_user_a')[0]
        spec=dict(schema_version='1.1',task_key='merchant-task',trace_id=req['trace_id'],run_at='2026-10-20T00:00:00Z',kind='execute_request',origin_action='budget.reserve',origin_idempotency_key=reservation['reserve_key'],payload=req)
        with self.store.connection(write=True) as c:
            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,NULL)',('task','demo_user_a','merchant-task',json.dumps(spec),'scheduled','demo_account_a'))
        self.assertEqual(self.release(rid)['error']['code'],'INVALID_STATE')
        with self.store.connection(write=True) as c: self.g.services.cancel_task(c,'task',principal_id='demo_user_a')
        self.approve(self.release(rid));self.now+=timedelta(days=2)
        self.assertEqual(self.send(req)['error']['code'],'INVALID_STATE');self.assertEqual(self.balance(),(200000,200000,0))

    def test_v9_migration_preserves_pending_and_existing_reservation(self):
        rid=self.reserve();pending=self.send(self.req('budget.reserve',dict(account_id='demo_account_a',amount_minor=100,purpose='旧待确认')))
        with self.store.connection(write=True) as c:
            for table in ('merchant_orders','merchant_intents','card_applications','subscription_reminders'): c.execute('DROP TABLE '+table)
            c.execute('PRAGMA user_version=9')
        initialize(self.store);self.assertEqual(self.balance(),(200000,150000,50000))
        self.assertEqual(self.approve(pending)['status'],'succeeded');self.assertEqual(self.approve(self.send(self.order(rid)))['status'],'succeeded')

    def test_consumption_reconciles_with_bill_and_reset_handles_new_tables(self):
        rid=self.reserve();self.approve(self.send(self.order(rid)))
        query=adapt_request('bill.analyze',dict(account_id='demo_account_a',start_date='2026-10-01',end_date='2026-10-31'),request_id='bill',trace_id='bill')
        result=self.g.execute(query,principal_id='demo_user_a');self.assertEqual(result['data']['total_expense_minor'],19900)
        from bank.seed import reset_demo
        with patch('bank.seed.demo_path',return_value=self.store.path): reset_demo(confirm=True)
        self.assertEqual(self.balance(),(200000,200000,0));self.assertEqual(self.g.list_orders(principal_id='demo_user_a'),[])


if __name__=='__main__': unittest.main()
