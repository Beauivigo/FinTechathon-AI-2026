from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import unittest
from agent.controller import Conversation
from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import adapt_request, ContractError, ServiceError
from streamlit.testing.v1 import AppTest


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'bank.db');initialize(self.store)
        self.now=datetime(2026,10,17,tzinfo=timezone.utc)
        self.g=BankGateway(self.store,clock=lambda:self.now)

    def request(self,decision='accept',sid='SUB-E001'):
        return adapt_request('subscription.review',dict(subscription_id=sid,decision=decision),
                             request_id='r',trace_id='t',idempotency_key=uuid4().hex)

    def listing(self,key=None,account='demo_account_a'):
        return adapt_request('subscription.list',dict(account_id=account),request_id=key or uuid4().hex,trace_id='t')

    def execute(self,req):return self.g.execute(req,principal_id='demo_user_a')

    def finish(self,pending,decision='approve'):
        n=pending['next_action']
        return self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],
            operation_digest=n['operation_digest'],decision=decision),principal_id='demo_user_a')

    def state(self):
        with self.store.connection() as c:
            return c.execute("SELECT subscription_state FROM subscriptions WHERE subscription_id='SUB-E001'").fetchone()[0]

    def test_read_requires_confirmation_without_write_key_and_recovers(self):
        req=self.listing('read_without_key'); pending=self.execute(req)
        self.assertEqual(pending['status'],'awaiting_confirmation');self.assertEqual(pending['data'],{})
        self.assertNotIn('模拟影音',str(pending))
        saved=self.g.get_confirmed_read('read_without_key',principal_id='demo_user_a')
        self.assertNotIn('idempotency_key',saved['operation_request'])
        self.assertEqual(saved['operation_result']['data'],{})
        self.assertFalse('subscriptions' in self.g.get_context(principal_id='demo_user_a'))
        with self.assertRaises(ServiceError):self.g.services.query('subscription.list',req['params'],principal_id='demo_user_a')
        self.g=BankGateway(self.store); result=self.finish(pending)
        self.assertEqual([s['subscription_id'] for s in result['data']['subscriptions']],['SUB-E001','SUB-E003'])
        self.assertEqual(self.execute(req),result)
        with self.assertRaises(ContractError):
            adapt_request('operation.get',dict(original_action='subscription.list',original_idempotency_key='read_without_key'),request_id='r',trace_id='t')

    def test_read_reject_expiry_ownership_and_content_conflict(self):
        req=self.listing();p=self.execute(req)
        self.assertEqual(self.finish(p,'reject')['status'],'cancelled')
        p=self.execute(self.listing());self.now+=timedelta(minutes=10)
        self.assertEqual(self.finish(p)['error']['code'],'AUTH_EXPIRED')
        self.assertEqual(self.execute(self.listing(account='demo_account_li'))['error']['code'],'FORBIDDEN')
        other=req|dict(params=dict(account_id='demo_account_li'))
        self.assertEqual(self.execute(other)['error']['code'],'IDEMPOTENCY_CONFLICT')
        with self.assertRaises(ServiceError):self.g.get_confirmed_read(req['request_id'],principal_id='demo_user_li')

    def test_accept_replay_conflict_and_illegal_state(self):
        req=self.request();p=self.execute(req)
        self.assertEqual(p['status'],'awaiting_confirmation')
        result=self.finish(p);self.assertEqual(result['data']['subscription_state'],'active')
        self.assertEqual(self.execute(req),result)
        altered=req|dict(params=dict(subscription_id='SUB-E001',decision='reject'))
        self.assertEqual(self.execute(altered)['error']['code'],'IDEMPOTENCY_CONFLICT')
        for sid in ('SUB-E001','SUB-E003'):
            self.assertEqual(self.execute(self.request(sid=sid))['error']['code'],'INVALID_STATE')
        initialize(self.store);self.assertEqual(self.state(),'active')

    def test_dismiss_is_not_cancel_and_no_money(self):
        self.assertEqual(self.finish(self.execute(self.request('reject')))['data']['subscription_state'],'dismissed')
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ledger_entries').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT SUM(balance_minor) FROM accounts').fetchone()[0],200000)

    def test_concurrent_review_only_one_change(self):
        pending=self.execute(self.request())
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.finish(pending),range(2)))
        self.assertEqual(results[0],results[1])
        with self.store.connection() as c:self.assertEqual(c.execute("SELECT revision FROM subscriptions WHERE subscription_id='SUB-E001'").fetchone()[0],1)

    def test_changed_record_reject_and_ownership(self):
        req=self.request();p=self.execute(req)
        self.assertEqual(self.g.execute(self.request(),principal_id='demo_user_li')['error']['code'],'FORBIDDEN')
        with self.store.connection(write=True) as c:c.execute("UPDATE subscriptions SET amount_minor=2501 WHERE subscription_id='SUB-E001'")
        self.assertEqual(self.finish(p)['error']['code'],'AUTH_INVALID')
        self.assertEqual(self.state(),'candidate')
        p=self.execute(self.request());self.assertEqual(self.finish(p,'reject')['status'],'cancelled')

    def test_audit_failure_rollback_and_lost_reply_recovery(self):
        p=self.execute(self.request());original=self.g._audit
        def fail(*args,**kwargs):
            if args[3]=='authorization_consumed':raise ServiceError('UPSTREAM_ERROR','injected')
            return original(*args,**kwargs)
        with patch.object(self.g,'_audit',side_effect=fail):self.assertEqual(self.finish(p)['status'],'failed')
        self.assertEqual(self.state(),'candidate')
        flow=Conversation(self.g,principal_id='demo_user_a');flow.start_action('subscription.review',dict(subscription_id='SUB-E001',decision='reject'))
        authorize=self.g.authorize
        def lost(*args,**kwargs):authorize(*args,**kwargs);raise TimeoutError('lost receipt')
        with patch.object(self.g,'authorize',side_effect=lost):flow.decide(True)
        self.assertTrue(flow.needs_reconciliation);self.assertEqual(self.state(),'dismissed')
        flow.recover();self.assertEqual(flow.result['status'],'succeeded')
        self.assertFalse(flow.needs_reconciliation)

    def test_read_replay_does_not_expose_changed_owner(self):
        req=self.listing();p=self.execute(req);self.finish(p)
        with self.store.connection(write=True) as c:c.execute("UPDATE accounts SET principal_id='demo_user_li' WHERE account_id='demo_account_a'")
        self.assertEqual(self.execute(req)['error']['code'],'FORBIDDEN')
        with self.assertRaises(ServiceError):self.finish(p)
        with self.assertRaises(ServiceError):self.g.get_confirmed_read(req['request_id'],principal_id='demo_user_a')

    def test_page_confirm_refresh_review_and_recover(self):
        root=Path(__file__).resolve().parents[1]
        with patch('bank.seed.demo_path',return_value=self.store.path),patch('app.demo.gateway',return_value=self.g):
            page=AppTest.from_file(str(root/'app/main.py')).run();page.switch_page('pages/5_订阅管理.py').run()
            self.assertFalse(page.dataframe)
            page.button(key='subscription_list').click().run();self.assertFalse(page.dataframe)
            page=AppTest.from_file(str(root/'app/main.py')).run();page.switch_page('pages/5_订阅管理.py').run()
            page.button(key='action_recover').click().run();page.button(key='action_approve').click().run()
            self.assertFalse(page.exception);self.assertEqual(len(page.dataframe[0].value),2)
            page.selectbox(key='subscription_decision').select('reject').run()
            page.button(key='subscription_review').click().run();self.assertEqual(self.state(),'candidate')
            page.button(key='action_approve').click().run();self.assertEqual(self.state(),'dismissed')
            self.assertFalse(page.exception)

    def test_v6_migration_preserves_card_pending(self):
        request=adapt_request('card.freeze',dict(card_id='CARD-E001'),request_id='r',trace_id='t',idempotency_key='old-card')
        p=self.execute(request)
        with self.store.connection(write=True) as c:
            c.execute('DROP TABLE subscriptions');c.execute("DELETE FROM metadata WHERE key='subscriptions_seed_version'")
            c.execute('PRAGMA user_version=6')
        initialize(self.store)
        n=p['next_action'];proof=self.g.issue_demo_proof(n['challenge_id'],principal_id='demo_user_a')
        result=self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve',proof_token=proof),principal_id='demo_user_a')
        self.assertEqual(result['status'],'succeeded')
