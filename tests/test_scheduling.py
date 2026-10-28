from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import unittest
from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from bank.scheduler import Scheduler, transition, owned_task
from shared import adapt_request, ServiceError
from tests.ui_support import AppTest


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'bank.db');initialize(self.store)
        self.now=datetime(2026,10,17,tzinfo=timezone.utc)
        self.g=BankGateway(self.store,clock=lambda:self.now)
        self.scheduler=Scheduler(self.g)

    def request(self,offset=timedelta(minutes=5),amount=20000):
        return adapt_request('transfer.schedule',dict(account_id='demo_account_a',payee_id='demo_payee_li',
            amount_minor=amount,execute_at=(self.now+offset).isoformat()),request_id=uuid4().hex,trace_id='schedule-test',idempotency_key=uuid4().hex)

    def execute(self,req):return self.g.execute(req,principal_id='demo_user_a')

    def finish(self,pending,approve=True):
        n=pending['next_action']
        p=dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve' if approve else 'reject')
        if approve and n['kind']=='verify':p['proof_token']=self.g.issue_demo_proof(n['challenge_id'],principal_id='demo_user_a')
        return self.g.authorize(p,principal_id='demo_user_a')

    def register(self,**kwargs):
        req=self.request(**kwargs);r=self.finish(self.execute(req));self.assertEqual(r['status'],'scheduled')
        return req,self.g.services.get_task(r['data']['schedule_id'],principal_id='demo_user_a')

    def task(self,t):return self.g.services.get_task(t['task_id'],principal_id='demo_user_a')
    def run_due(self):return self.scheduler.run_once(principal_id='demo_user_a')
    def query_execution(self,t):
        req=adapt_request('operation.get',dict(original_action='transfer.create',original_idempotency_key=t['spec']['payload']['idempotency_key']),request_id='q',trace_id='q')
        return self.execute(req)

    def money(self):
        with self.store.connection() as c:
            return (tuple(c.execute("SELECT balance_minor,available_minor,reserved_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()),
                    c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0],c.execute('SELECT COUNT(*) FROM ledger_entries').fetchone()[0])

    def test_registration_bounds_and_no_money_replay(self):
        for offset in (timedelta(minutes=1),timedelta(days=30)):
            req,t=self.register(offset=offset)
            self.assertEqual(self.execute(req)['data']['schedule_id'],t['task_id'])
            self.assertNotEqual(t['spec']['payload']['idempotency_key'],req['idempotency_key'])
        for offset in (timedelta(seconds=59),timedelta(days=30,microseconds=1),timedelta(seconds=-1)):
            self.assertEqual(self.execute(self.request(offset=offset))['error']['code'],'INVALID_REQUEST')
        self.assertEqual(self.money(),((200000,200000,0),0,0))

    def test_registration_reject_expire_ownership_and_conflict(self):
        req=self.request();p=self.execute(req)
        changed=deepcopy(req);changed['params']['amount_minor']=1
        self.assertEqual(self.execute(changed)['error']['code'],'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.finish(p,False)['status'],'cancelled')
        req=self.request(offset=timedelta(hours=1));p=self.execute(req);self.now+=timedelta(minutes=10)
        self.assertEqual(self.finish(p)['error']['code'],'AUTH_EXPIRED')
        self.assertEqual(self.g.execute(self.request(),principal_id='demo_user_li')['error']['code'],'FORBIDDEN')
        self.assertEqual(self.scheduler.list_tasks(principal_id='demo_user_a'),[])

    def test_confirmation_rechecks_time_and_payee(self):
        p=self.execute(self.request(offset=timedelta(minutes=1)));self.now+=timedelta(seconds=1)
        self.assertEqual(self.finish(p)['error']['code'],'INVALID_REQUEST')
        p=self.execute(self.request())
        with self.store.connection(write=True) as c:c.execute("UPDATE payees SET account_id='demo_account_wang_1' WHERE payee_id='demo_payee_li'")
        self.assertEqual(self.finish(p)['error']['code'],'AUTH_INVALID')

    def test_early_due_authorization_no_inherited_approval(self):
        _,t=self.register();self.now+=timedelta(minutes=5,seconds=-1)
        self.assertEqual(self.run_due(),[]);self.assertEqual(self.task(t)['state'],'scheduled')
        self.now+=timedelta(seconds=1);self.run_due()
        self.assertEqual(self.task(t)['state'],'dispatched')
        pending=self.query_execution(t)['data']['operation_result']
        self.assertEqual(pending['status'],'awaiting_confirmation');self.assertEqual(self.money(),((200000,200000,0),0,0))
        self.assertEqual(self.finish(pending)['status'],'succeeded')
        self.assertEqual(self.money(),((180000,180000,0),1,2))
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT SUM(balance_minor) FROM accounts').fetchone()[0],200000)

    def test_lateness_exact_five_minutes_and_expired(self):
        _,t=self.register();self.now+=timedelta(minutes=10);self.run_due()
        self.assertEqual(self.task(t)['state'],'dispatched')
        _,late=self.register();self.now+=timedelta(minutes=10,microseconds=1);self.run_due()
        self.assertEqual(self.task(late)['state'],'failed')
        self.assertEqual(self.query_execution(late)['error']['code'],'NOT_FOUND')
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT failure_reason FROM tasks WHERE task_id=?',(late['task_id'],)).fetchone()[0],'lateness_expired')
        self.assertEqual(self.money(),((200000,200000,0),0,0))

    def test_due_uses_current_day_cumulative_and_balance(self):
        _,t=self.register(amount=60000)
        req=adapt_request('transfer.create',dict(account_id='demo_account_a',payee_id='demo_payee_li',amount_minor=80000),request_id='other',trace_id='other',idempotency_key='other')
        self.finish(self.execute(req));self.assertEqual(self.money()[0][0],120000)
        self.now+=timedelta(minutes=5);self.run_due()
        pending=self.query_execution(t)['data']['operation_result']
        self.assertEqual(pending['status'],'awaiting_verification')
        self.assertEqual(self.finish(pending)['status'],'succeeded');self.assertEqual(self.money(),((60000,60000,0),2,4))

    def test_due_confirmation_can_escalate_and_expires_after_ten_minutes(self):
        _,t=self.register(amount=60000);self.now+=timedelta(minutes=5);self.run_due()
        pending=self.query_execution(t)['data']['operation_result']
        req=adapt_request('transfer.create',dict(account_id='demo_account_a',payee_id='demo_payee_li',amount_minor=80000),request_id='other',trace_id='other',idempotency_key='other')
        self.finish(self.execute(req));upgraded=self.finish(pending)
        self.assertEqual(upgraded['status'],'awaiting_verification')
        n=upgraded['next_action'];self.now+=timedelta(minutes=10)
        result=self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve'),principal_id='demo_user_a')
        self.assertEqual(result['error']['code'],'AUTH_EXPIRED');self.assertEqual(self.money(),((120000,120000,0),1,2))

    def test_registration_dynamic_gate_and_next_shanghai_day(self):
        req=adapt_request('transfer.create',dict(account_id='demo_account_a',payee_id='demo_payee_li',amount_minor=100000),request_id='other',trace_id='other',idempotency_key='other')
        self.finish(self.execute(req))
        req=self.request(offset=timedelta(days=1),amount=1);p=self.execute(req)
        self.assertEqual(p['status'],'awaiting_verification')
        r=self.finish(p);t=self.g.services.get_task(r['data']['schedule_id'],principal_id='demo_user_a')
        self.now+=timedelta(days=1);self.run_due()
        self.assertEqual(self.query_execution(t)['data']['operation_result']['status'],'awaiting_confirmation')

    def test_due_insufficient_funds_and_changed_mapping_never_pay(self):
        _,t=self.register(amount=200001);self.now+=timedelta(minutes=5);self.run_due()
        self.assertEqual(self.task(t)['state'],'failed')
        _,t=self.register()
        with self.store.connection(write=True) as c:c.execute("UPDATE payees SET account_id='demo_account_wang_1' WHERE payee_id='demo_payee_li'")
        self.now+=timedelta(minutes=5);self.run_due();self.assertEqual(self.task(t)['state'],'failed')
        self.assertEqual(self.money(),((200000,200000,0),0,0))

    def test_cancel_and_owner_and_no_direct_early_execution(self):
        req,t=self.register()
        with self.assertRaises(ServiceError):self.g.services.get_task(t['task_id'],principal_id='demo_user_li')
        with self.store.connection(write=True) as c:
            with self.assertRaises(ServiceError):self.g.services.cancel_task(c,t['task_id'],principal_id='demo_user_li')
            cancelled=self.g.services.cancel_task(c,t['task_id'],principal_id='demo_user_a')
            self.assertEqual(cancelled['state'],'cancelled')
        self.assertEqual(self.execute(req)['status'],'scheduled')
        self.now+=timedelta(minutes=5);self.run_due()
        self.assertEqual(self.execute(t['spec']['payload'])['error']['code'],'INVALID_STATE')
        self.assertEqual(self.money(),((200000,200000,0),0,0))

    def test_concurrent_dispatch_cancel_and_confirmation(self):
        _,t=self.register();self.now+=timedelta(minutes=5)
        def cancel():
            try:
                with self.store.connection(write=True) as c:return self.g.services.cancel_task(c,t['task_id'],principal_id='demo_user_a')['state']
            except ServiceError:return 'in_flight'
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures=[pool.submit(self.run_due),pool.submit(self.run_due),pool.submit(cancel)]
            for f in futures:f.result()
        state=self.task(t)['state'];self.assertIn(state,('dispatched','cancelled'))
        if state=='dispatched':
            pending=self.query_execution(t)['data']['operation_result']
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.finish(pending),range(2)))
            self.assertEqual(results[0],results[1]);self.assertEqual(self.money(),((180000,180000,0),1,2))
        else:self.assertEqual(self.money(),((200000,200000,0),0,0))
        with self.store.connection() as c:
            self.assertLessEqual(c.execute("SELECT COUNT(*) FROM audit_events WHERE outcome='task_running'").fetchone()[0],1)

    def test_claim_crash_restart_and_lost_dispatch_reply(self):
        _,t=self.register();self.now+=timedelta(minutes=5)
        with self.store.connection(write=True) as c:transition(c,owned_task(c,t['task_id'],'demo_user_a'),'running',self.now)
        initialize(self.store);self.g=BankGateway(self.store,clock=lambda:self.now);self.scheduler=Scheduler(self.g)
        execute=self.g.execute
        def lost(req,**kwargs):
            reply=execute(req,**kwargs)
            if req['action']=='transfer.create':raise TimeoutError('after commit')
            return reply
        with patch.object(self.g,'execute',side_effect=lost):self.run_due()
        self.assertEqual(self.task(t)['state'],'unknown')
        pending=self.query_execution(t)['data']['operation_result'];self.finish(pending)
        self.now+=timedelta(minutes=20);self.run_due()
        self.assertEqual(self.task(t)['state'],'dispatched');self.assertEqual(self.money(),((180000,180000,0),1,2))
        self.run_due();self.assertEqual(self.money(),((180000,180000,0),1,2))

    def test_unknown_query_failure_never_blindly_resends(self):
        _,t=self.register();self.now+=timedelta(minutes=5)
        calls=[];execute=self.g.execute
        def broken(req,**kwargs):
            calls.append(req['action'])
            if req['action']=='operation.get':raise TimeoutError('unavailable')
            return execute(req,**kwargs)
        with patch.object(self.g,'execute',side_effect=broken):self.run_due();self.run_due()
        self.assertEqual(calls,['operation.get','operation.get']);self.assertEqual(self.task(t)['state'],'unknown')
        self.run_due();self.assertEqual(self.task(t)['state'],'dispatched')

    def test_failed_registration_audit_rolls_back_task(self):
        p=self.execute(self.request());audit=self.g._audit
        def fail(*args,**kwargs):
            if args[3]=='authorization_consumed':raise ServiceError('UPSTREAM_ERROR','injected')
            return audit(*args,**kwargs)
        with patch.object(self.g,'_audit',side_effect=fail):self.assertEqual(self.finish(p)['status'],'failed')
        self.assertEqual(self.scheduler.list_tasks(principal_id='demo_user_a'),[])

    def test_lost_before_submission_reuses_key_and_cannot_cancel_in_flight(self):
        _,t=self.register();self.now+=timedelta(minutes=5);execute=self.g.execute
        def lost(req,**kwargs):
            if req['action']=='transfer.create':raise TimeoutError('before submit')
            return execute(req,**kwargs)
        with patch.object(self.g,'execute',side_effect=lost):self.run_due()
        self.assertEqual(self.task(t)['state'],'unknown')
        with self.store.connection(write=True) as c:
            with self.assertRaises(ServiceError):self.g.services.cancel_task(c,t['task_id'],principal_id='demo_user_a')
        self.run_due();self.assertEqual(self.task(t)['state'],'dispatched')
        pending=self.query_execution(t)['data']['operation_result'];self.finish(pending)
        self.assertEqual(self.money(),((180000,180000,0),1,2))

    def test_late_failure_cannot_hide_concurrently_created_execution(self):
        _,t=self.register();self.now+=timedelta(minutes=5)
        with self.store.connection(write=True) as c:transition(c,owned_task(c,t['task_id'],'demo_user_a'),'running',self.now)
        p=self.execute(t['spec']['payload'])
        self.scheduler._mark(t['task_id'],'demo_user_a','failed','lateness_expired')
        self.assertEqual(self.task(t)['state'],'dispatched')
        self.finish(p);self.assertEqual(self.money(),((180000,180000,0),1,2))

    def test_due_owner_changed_is_failed_without_execution(self):
        _,t=self.register()
        with self.store.connection(write=True) as c:c.execute("UPDATE accounts SET principal_id='demo_user_li' WHERE account_id='demo_account_a'")
        self.now+=timedelta(minutes=5);self.run_due()
        self.assertEqual(self.task(t)['state'],'failed')
        self.assertEqual(self.query_execution(t)['error']['code'],'NOT_FOUND')
        self.assertEqual(self.money(),((200000,200000,0),0,0))

    def test_task_service_validates_transaction_and_original_content(self):
        req,t=self.register()
        with self.assertRaises(ServiceError):self.g.services.register_task(None,t['spec'],principal_id='demo_user_a')
        altered=deepcopy(t['spec']);altered['payload']['params']['amount_minor']=1
        with self.store.connection(write=True) as c:
            with self.assertRaises(ServiceError):self.g.services.register_task(c,altered,principal_id='demo_user_a')
            self.assertEqual(self.g.services.register_task(c,t['spec'],principal_id='demo_user_a'),t)

    def test_v7_migration_preserves_pending_registration(self):
        req=self.request();p=self.execute(req)
        with self.store.connection(write=True) as c:c.execute('DROP TABLE tasks');c.execute('PRAGMA user_version=7')
        initialize(self.store);self.assertEqual(self.finish(p)['status'],'scheduled')

    def test_page_registration_dispatch_payment_refresh(self):
        root=Path(__file__).resolve().parents[1]
        with patch('bank.seed.demo_path',return_value=self.store.path),patch('app.demo.gateway',return_value=self.g):
            page=AppTest.from_file(str(root/'app/main.py')).run();page.switch_page('pages/6_定时转账.py').run()
            people=__import__('app.workflow',fromlist=['demo_payees']).demo_payees(page.session_state['action_flow'])
            page.selectbox(key='schedule_payee').select(people[0])
            page.text_input(key='schedule_amount').set_value('200.00')
            page.button(key='schedule_submit').click().run();page.button(key='action_approve').click().run()
            self.assertFalse(page.exception);self.assertEqual(self.money(),((200000,200000,0),0,0))
            self.now+=timedelta(minutes=5);page.button(key='schedule_run').click().run()
            self.assertFalse(page.exception)
            page=AppTest.from_file(str(root/'app/main.py')).run();page.switch_page('pages/6_定时转账.py').run()
            page.button(key='schedule_execution').click().run();page.button(key='action_approve').click().run()
            self.assertFalse(page.exception);self.assertEqual(self.money(),((180000,180000,0),1,2))
