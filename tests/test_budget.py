from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import json
import unittest

from agent.controller import Conversation
from bank.gateway import BankGateway
from bank.seed import initialize, reset_demo
from bank.store import Store, STORAGE_VERSION
from shared import adapt_request, adapt_result, operation_digest, ServiceError, ContractError


class BudgetTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name)/'bank.db'); initialize(self.store)
        self.now = datetime(2026,10,17,tzinfo=timezone.utc)
        self.g = BankGateway(self.store, clock=lambda:self.now)

    def request(self, amount=30000, rid=None, key=None, account='demo_account_a'):
        return adapt_request('budget.release' if rid else 'budget.reserve',
            dict(reservation_id=rid) if rid else dict(account_id=account, amount_minor=amount, purpose='模拟生日'),
            request_id=uuid4().hex, trace_id=uuid4().hex, idempotency_key=key or uuid4().hex)

    def execute(self, request):
        return self.g.execute(request, principal_id='demo_user_a')

    def finish(self, pending, decision='approve'):
        n = pending['next_action']
        return self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],
            operation_digest=n['operation_digest'],decision=decision), principal_id='demo_user_a')

    def reserve(self, amount=30000):
        return self.finish(self.execute(self.request(amount)))['data']['reservation_id']

    def balances(self):
        with self.store.connection() as c:
            return tuple(c.execute("SELECT balance_minor,available_minor,reserved_minor FROM accounts WHERE account_id='demo_account_a'").fetchone())

    def test_confirm_reserve_release_exact_contract_audit_and_no_ledger(self):
        req = self.request(); pending = self.execute(req)
        self.assertEqual(pending['status'], 'awaiting_confirmation')
        self.assertEqual(self.balances(), (200000,200000,0))
        result = self.finish(pending); rid = result['data']['reservation_id']
        self.assertEqual(set(result['data']), {'reservation_id','amount_minor'})
        self.assertEqual(self.balances(), (200000,170000,30000))
        release = self.request(rid=rid); p = self.execute(release)
        self.assertEqual(p['status'], 'awaiting_confirmation')
        released = self.finish(p)
        self.assertEqual(released['data'], {'reservation_id':rid})
        self.assertEqual(self.balances(), (200000,200000,0))
        self.assertEqual(self.execute(release),released)
        self.assertEqual(self.execute(self.request(rid=rid))['error']['code'],'INVALID_STATE')
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM ledger_entries').fetchone()[0],0)
            details = [json.loads(r[0]) for r in c.execute("SELECT details_json FROM audit_events WHERE outcome='succeeded'")]
            self.assertEqual([d['effect'] for d in details],['reserve','release_reserved'])
            self.assertEqual([d['amount_minor'] for d in details],[30000,30000])
            self.assertEqual(len({d['posting_key'] for d in details}),2)
            self.assertEqual(c.execute('SELECT state FROM reservations').fetchone()[0],'released')

    def test_replay_conflict_and_two_independent_reservations(self):
        req = self.request(); result = self.finish(self.execute(req))
        self.assertEqual(self.execute(req),result)
        altered = req | dict(params=req['params'] | dict(amount_minor=30001))
        self.assertEqual(self.execute(altered)['error']['code'],'IDEMPOTENCY_CONFLICT')
        other = self.reserve(50000)
        self.finish(self.execute(self.request(rid=result['data']['reservation_id'])))
        self.assertEqual(self.balances(),(200000,150000,50000))
        self.assertEqual(self.g.list_reservations(principal_id='demo_user_a')[0]['reservation_id'],other)

    def test_reject_expiry_and_late_approval_cannot_mutate(self):
        p = self.execute(self.request())
        self.assertEqual(self.finish(p,'reject')['status'],'cancelled')
        self.assertEqual(self.finish(p)['status'],'cancelled')
        p = self.execute(self.request()); self.now += timedelta(minutes=10)
        self.assertEqual(self.finish(p)['error']['code'],'AUTH_EXPIRED')
        self.assertEqual(self.finish(p)['error']['code'],'AUTH_EXPIRED')
        self.assertEqual(self.balances(),(200000,200000,0))
        rid = self.reserve(); p = self.execute(self.request(rid=rid))
        self.assertEqual(self.finish(p,'reject')['status'],'cancelled')
        self.assertEqual(self.balances(),(200000,170000,30000))

    def test_ownership_forged_authorization_and_invalid_amounts(self):
        self.assertEqual(self.execute(self.request(account='demo_account_li'))['error']['code'],'FORBIDDEN')
        rid = self.reserve()
        self.assertEqual(self.g.execute(self.request(rid=rid),principal_id='demo_user_li')['error']['code'],'FORBIDDEN')
        self.assertEqual(self.g.list_reservations(principal_id='demo_user_li'),[])
        p = self.execute(self.request()); n=p['next_action']
        with self.assertRaises(ServiceError):
            self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve'),principal_id='demo_user_li')
        for amount in (0,-1,True,1.5,9007199254740992):
            with self.assertRaises(ContractError): self.request(amount)
        self.assertEqual(self.balances(),(200000,170000,30000))

    def test_balance_rechecked_after_confirmation_and_normal_transfer_respects_reserve(self):
        p = self.execute(self.request(180000)); self.reserve(30000)
        self.assertEqual(self.finish(p)['error']['code'],'INSUFFICIENT_FUNDS')
        request = adapt_request('transfer.create',dict(account_id='demo_account_a',payee_id='demo_payee_li',amount_minor=180000),request_id='r',trace_id='t',idempotency_key='transfer')
        p = self.execute(request); n=p['next_action']
        proof = self.g.issue_demo_proof(n['challenge_id'],principal_id='demo_user_a')
        result = self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve',proof_token=proof),principal_id='demo_user_a')
        self.assertEqual(result['error']['code'],'INSUFFICIENT_FUNDS')
        self.assertEqual(self.balances(),(200000,170000,30000))

    def test_concurrent_reserves_and_releases_do_not_overuse(self):
        pending = [self.execute(self.request(150000)) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.finish,pending))
        self.assertEqual(sorted(r['status'] for r in results),['failed','succeeded'])
        rid = next(r['data']['reservation_id'] for r in results if r['status']=='succeeded')
        pending = [self.execute(self.request(rid=rid)) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.finish,pending))
        self.assertEqual(sorted(r['status'] for r in results),['failed','succeeded'])
        self.assertEqual(self.balances(),(200000,200000,0))

    def test_duplicate_authorization_replays_once(self):
        p = self.execute(self.request())
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _:self.finish(p),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(self.balances(),(200000,170000,30000))

    def test_reserve_and_release_rollback_if_receipt_or_audit_fails(self):
        for action in ('budget.reserve','budget.release'):
            rid = self.reserve() if action=='budget.release' else None
            before = self.balances(); p=self.execute(self.request(rid=rid))
            original = self.g._audit
            def fail(*args,**kwargs):
                if args[3]=='authorization_consumed': raise ServiceError('UPSTREAM_ERROR','injected')
                return original(*args,**kwargs)
            with patch.object(self.g,'_audit',side_effect=fail): result=self.finish(p)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(self.balances(),before)
            with self.store.connection() as c:
                self.assertEqual(c.execute("SELECT count(*) FROM audit_events WHERE action=? AND outcome='succeeded'",(action,)).fetchone()[0],1 if action=='budget.reserve' and rid else 0)
        # Result persistence failure also rolls back the release mutation.
        p=self.execute(self.request(rid=rid)); original=self.g._save
        def fail_save(c,request,result,*args):
            if result['status']=='succeeded': raise ServiceError('UPSTREAM_ERROR','injected')
            return original(c,request,result,*args)
        with patch.object(self.g,'_save',side_effect=fail_save): self.assertEqual(self.finish(p)['status'],'failed')
        self.assertEqual(self.balances(),before)

    def test_committed_reply_lost_recover_restart_without_second_release(self):
        flow=Conversation(self.g,principal_id='demo_user_a')
        flow.start_action('budget.reserve',dict(account_id='demo_account_a',amount_minor=30000,purpose='模拟生日'))
        authorize=self.g.authorize
        def lost(*args,**kwargs):
            authorize(*args,**kwargs)
            raise ServiceError('RESULT_UNKNOWN','lost after commit')
        with patch.object(self.g,'authorize',side_effect=lost): flow.decide(True)
        self.assertTrue(flow.needs_reconciliation)
        with self.assertRaises(ValueError): flow.start_action('budget.reserve',dict(account_id='demo_account_a',amount_minor=1,purpose='重试'))
        flow.recover(); rid=flow.result['data']['reservation_id']
        flow.start_action('budget.release',dict(reservation_id=rid))
        with patch.object(self.g,'authorize',side_effect=lost): flow.decide(True)
        key=flow.request['idempotency_key']; self.assertTrue(flow.needs_reconciliation)
        initialize(self.store)
        restarted=Conversation(BankGateway(self.store,clock=lambda:self.now),principal_id='demo_user_a')
        restarted.recover(action='budget.release',idempotency_key=key)
        self.assertEqual(restarted.result['status'],'succeeded')
        self.assertEqual(self.balances(),(200000,200000,0))
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM audit_events WHERE outcome='succeeded' AND action='budget.release'").fetchone()[0],1)

    def test_unknown_source_or_frozen_budget_never_released(self):
        rid=self.reserve(); before=self.balances()
        with self.store.connection(write=True) as c:
            c.execute("UPDATE reservations SET state='frozen' WHERE reservation_id=?",(rid,))
        self.assertEqual(self.execute(self.request(rid=rid))['error']['code'],'INVALID_STATE')
        with self.store.connection(write=True) as c:
            c.execute("UPDATE reservations SET state='active' WHERE reservation_id=?",(rid,))
            saved=c.execute("SELECT * FROM operations WHERE action='budget.reserve'").fetchone()
            req=json.loads(saved['request_json'])
            unknown=adapt_result(req,dict(status='unknown',error=dict(code='RESULT_UNKNOWN',message='test')),source='sandbox')
            c.execute("UPDATE operations SET result_json=? WHERE action='budget.reserve'",(json.dumps(unknown),))
        self.assertEqual(self.execute(self.request(rid=rid))['error']['code'],'INVALID_STATE')
        self.assertEqual(self.balances(),before)
        with self.store.connection(write=True) as c:
            c.execute("DELETE FROM challenges WHERE action='budget.reserve'")
            c.execute("DELETE FROM operations WHERE action='budget.reserve'")
        self.assertEqual(self.execute(self.request(rid=rid))['error']['code'],'INVALID_STATE')
        self.assertEqual(self.balances(),before)

    def test_explicit_reset_with_budget_and_failed_reset_are_atomic(self):
        self.reserve()
        with patch('bank.seed.demo_path',return_value=self.store.path):
            with patch('bank.seed._seed',side_effect=RuntimeError('injected')):
                with self.assertRaises(RuntimeError): reset_demo(confirm=True)
            self.assertEqual(self.balances(),(200000,170000,30000))
            self.assertEqual(len(self.g.list_reservations(principal_id='demo_user_a')),1)
            reset_demo(confirm=True)
        self.assertEqual(self.balances(),(200000,200000,0))
        self.assertEqual(self.g.list_reservations(principal_id='demo_user_a'),[])

    def merchant(self,rid):
        return adapt_request('merchant.order',dict(account_id='demo_account_a',reservation_id=rid,item_id='flower',amount_minor=18000,deliver_on='2026-10-20',delivery_address_id='address'),request_id='r',trace_id='t',idempotency_key=uuid4().hex)

    def test_merchant_unknown_after_release_confirmation_blocks_mutation(self):
        rid=self.reserve(); p=self.execute(self.request(rid=rid)); req=self.merchant(rid)
        result=adapt_result(req,dict(status='unknown',error=dict(code='RESULT_UNKNOWN',message='test')),source='sandbox')
        with self.store.connection(write=True) as c:
            c.execute('INSERT INTO operations VALUES (?,?,?,?,?,?)',('demo_user_a','merchant.order',req['idempotency_key'],operation_digest(req,principal_id='demo_user_a'),json.dumps(req),json.dumps(result)))
        self.assertEqual(self.finish(p)['error']['code'],'INVALID_STATE')
        self.assertEqual(self.balances(),(200000,170000,30000))

    def test_scheduled_merchant_association_blocks_release(self):
        rid=self.reserve(); req=self.merchant(rid)
        row=self.g.list_reservations(principal_id='demo_user_a')[0]
        spec=dict(schema_version='1.1',task_key='taskkey',trace_id='t',run_at='2026-10-18T00:00:00Z',kind='execute_request',origin_action='budget.reserve',origin_idempotency_key=row['reserve_key'],payload=req)
        with self.store.connection(write=True) as c:
            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)',('task','demo_user_a','taskkey',json.dumps(spec),'scheduled','demo_account_li',None))
        self.assertEqual(self.execute(self.request(rid=rid))['error']['code'],'INVALID_STATE')
        self.assertEqual(self.balances(),(200000,170000,30000))

    def test_v8_migration_preserves_pending_and_old_balance(self):
        req=adapt_request('transfer.create',dict(account_id='demo_account_a',payee_id='demo_payee_li',amount_minor=100),request_id='r',trace_id='t',idempotency_key='old')
        pending=self.execute(req)
        with self.store.connection(write=True) as c:
            c.execute('DROP TABLE reservations'); c.execute('PRAGMA user_version=8')
        initialize(self.store); initialize(self.store)
        self.assertEqual(self.balances(),(200000,200000,0))
        self.assertEqual(self.finish(pending)['status'],'succeeded')
        with self.store.connection() as c:self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],STORAGE_VERSION)

    def test_page_confirmation_refresh_recovery_and_release(self):
        from tests.ui_support import AppTest
        root=Path(__file__).resolve().parents[1]
        with patch('bank.seed.demo_path',return_value=self.store.path),patch('app.demo.gateway',return_value=self.g):
            page=AppTest.from_file(str(root/'app/main.py')).run()
            page.switch_page('pages/7_预算管理.py').run()
            self.assertFalse(page.exception)
            page.text_input(key='budget_amount').set_value('300.00')
            page.text_input(key='budget_purpose').set_value('本次预算')
            page.button(key='budget_reserve').click().run()
            self.assertEqual(self.balances(),(200000,200000,0))
            self.assertTrue(page.button(key='budget_reserve').disabled)
            # 模拟刷新，重建会话后从原挑战继续确认。
            page=AppTest.from_file(str(root/'app/main.py')).run()
            page.switch_page('pages/7_预算管理.py').run()
            page.button(key='action_recover').click().run()
            page.button(key='action_approve').click().run()
            self.assertFalse(page.exception)
            self.assertEqual(self.balances(),(200000,170000,30000))
            self.assertEqual([m.value for m in page.metric if m.label=='剩余预留（元）'], ['300.00元'])
            self.assertTrue(any('本次预算' == m.value for m in page.markdown))
            page.button(key='budget_release').click().run()
            self.assertEqual(self.balances(),(200000,170000,30000))
            page.button(key='action_approve').click().run()
            self.assertFalse(page.exception)
            self.assertEqual(self.balances(),(200000,200000,0))
            self.assertEqual([m.value for m in page.metric if m.label=='剩余预留（元）'], ['0.00元'])
            self.assertTrue(any('badge[已释放]' in m.value for m in page.markdown))

    def test_page_rejection_and_lost_response_blocks_new_actions(self):
        from tests.ui_support import AppTest
        root=Path(__file__).resolve().parents[1]
        with patch('bank.seed.demo_path',return_value=self.store.path),patch('app.demo.gateway',return_value=self.g):
            page=AppTest.from_file(str(root/'app/main.py')).run()
            page.switch_page('pages/7_预算管理.py').run()
            page.text_input(key='budget_amount').set_value('300.00')
            page.text_input(key='budget_purpose').set_value('本次预算')
            page.button(key='budget_reserve').click().run()
            page.button(key='action_reject').click().run()
            self.assertEqual(self.balances(),(200000,200000,0))
            page.text_input(key='budget_amount').set_value('300.00')
            page.text_input(key='budget_purpose').set_value('本次预算')
            page.button(key='budget_reserve').click().run()
            authorize=self.g.authorize
            def lost(*args,**kwargs):
                authorize(*args,**kwargs)
                raise ServiceError('RESULT_UNKNOWN','lost response')
            with patch.object(self.g,'authorize',side_effect=lost): page.button(key='action_approve').click().run()
            self.assertFalse(page.exception)
            self.assertTrue(page.button(key='budget_reserve').disabled)
            self.assertTrue(page.button(key='budget_release').disabled)
            self.assertEqual(self.balances(),(200000,170000,30000))
            page.button(key='action_query').click().run()
            self.assertFalse(page.exception)
            self.assertFalse(page.button(key='budget_release').disabled)


if __name__=='__main__': unittest.main()
