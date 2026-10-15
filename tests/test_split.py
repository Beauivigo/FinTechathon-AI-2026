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
from bank.transfer import split_shares
from shared import adapt_request, ContractError, ServiceError
from shared.contracts import MAX_SAFE_INTEGER
from streamlit.testing.v1 import AppTest

PEOPLE = ['demo_payee_li', 'demo_payee_wang_1', 'demo_payee_wang_2']


class SplitTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'bank.db')
        initialize(self.store)
        self.now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        self.g = BankGateway(self.store, clock=lambda: self.now)

    def request(self, amount=1001, people=None):
        return adapt_request('transfer.split', dict(account_id='demo_account_a',
            payee_ids=PEOPLE if people is None else people, amount_minor=amount),
            request_id='r', trace_id='t', idempotency_key=uuid4().hex)

    def start(self, req):
        return self.g.execute(req, principal_id='demo_user_a')

    def finish(self, pending, decision='approve'):
        n = pending['next_action']
        return self.g.authorize(dict(schema_version='1.1', challenge_id=n['challenge_id'],
            operation_digest=n['operation_digest'], decision=decision), principal_id='demo_user_a')

    def assert_no_payment(self, collections):
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM collections').fetchone()[0], collections)
            for table in ('transfers', 'ledger_entries', 'fund_postings'):
                self.assertEqual(c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT balance_minor,available_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[:], (200000,200000))

    def test_exact_order_remainders_large_amounts_and_validation(self):
        for amount in (3, 1000, 1001, MAX_SAFE_INTEGER):
            req = self.request(amount, list(reversed(PEOPLE)))
            shares = split_shares(req)
            self.assertEqual([s['payee_id'] for s in shares], list(reversed(PEOPLE)))
            q, r = divmod(amount, 3)
            self.assertEqual([s['amount_minor'] for s in shares], [q + (i < r) for i in range(3)])
            self.assertEqual(sum(s['amount_minor'] for s in shares), amount)
        for people, amount in [([], 100), ([PEOPLE[0]] * 2, 100), (PEOPLE, 2)]:
            with self.assertRaises(ContractError):
                self.request(amount, people)

    def test_create_once_concurrently_and_restart(self):
        req = self.request()
        pending = self.start(req)
        self.assertEqual(pending['status'], 'awaiting_confirmation')
        self.assert_no_payment(0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.finish(pending), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]['status'], 'succeeded')
        self.assertEqual([s['amount_minor'] for s in results[0]['data']['shares']], [334,334,333])
        self.g = BankGateway(self.store)
        self.assertEqual(self.start(req), results[0])
        changed = req | dict(params=req['params'] | dict(payee_ids=list(reversed(PEOPLE))))
        self.assertEqual(self.start(changed)['error']['code'], 'IDEMPOTENCY_CONFLICT')
        self.assert_no_payment(1)

    def test_ownership_mapping_change_reject_and_expiry(self):
        self.assertEqual(self.start(self.request(100, ['missing']))['error']['code'], 'FORBIDDEN')
        req = self.request()
        self.assertEqual(self.g.execute(req, principal_id='demo_user_li')['error']['code'], 'FORBIDDEN')
        pending = self.start(req)
        with self.store.connection(write=True) as c:
            c.execute("UPDATE payees SET account_id='demo_account_wang_2' WHERE payee_id='demo_payee_li'")
        self.assertEqual(self.finish(pending)['error']['code'], 'AUTH_INVALID')
        self.assertEqual(self.finish(self.start(self.request()), 'reject')['status'], 'cancelled')
        pending = self.start(self.request())
        self.now += timedelta(minutes=10)
        self.assertEqual(self.finish(pending)['error']['code'], 'AUTH_EXPIRED')
        self.assert_no_payment(0)

    def test_failed_receipt_rolls_back_collection(self):
        pending = self.start(self.request())
        original = self.g._audit
        def fail(*args, **kwargs):
            if args[3] == 'authorization_consumed':
                raise ServiceError('UPSTREAM_ERROR', 'injected')
            return original(*args, **kwargs)
        with patch.object(self.g, '_audit', side_effect=fail):
            self.assertEqual(self.finish(pending)['status'], 'failed')
        self.assert_no_payment(0)

    def test_lost_reply_blocks_new_request_until_recovered(self):
        flow = Conversation(self.g, principal_id='demo_user_a')
        flow.start_action('transfer.split', self.request()['params'])
        original = self.g.authorize
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise ConnectionError('reply lost')
        with patch.object(self.g, 'authorize', side_effect=lost):
            flow.decide(True)
        self.assertEqual(flow.result['status'], 'unknown')
        with self.assertRaises(ValueError):
            flow.start_action('transfer.split', self.request()['params'])
        flow.recover()
        self.assertEqual(flow.result['status'], 'succeeded')
        self.assert_no_payment(1)

    def test_page_confirmation_and_fresh_session_recovery(self):
        page = str(Path(__file__).resolve().parents[1] / 'app/pages/4_AA收款.py')
        with patch('app.demo.gateway', return_value=self.g):
            app = AppTest.from_file(page).run()
            self.assertFalse(app.exception)
            app.text_input(key='split_amount').set_value('0.001').run()
            self.assertTrue(app.button(key='split_submit').disabled)
            app.text_input(key='split_amount').set_value('10.01').run()
            app.button(key='split_submit').click().run()
            self.assert_no_payment(0)
            app = AppTest.from_file(page).run()
            app.button(key='action_recover').click().run()
            app.button(key='action_approve').click().run()
            self.assertFalse(app.exception)
            self.assert_no_payment(1)

    def test_unreconciled_other_page_blocks_new_writes(self):
        other = Conversation(self.g, principal_id='demo_user_a')
        other.needs_reconciliation = True
        root = Path(__file__).resolve().parents[1]
        with patch('app.demo.gateway', return_value=self.g):
            app = AppTest.from_file(str(root / 'app/pages/4_AA收款.py'))
            app.session_state['conversation'] = other
            app.run()
            self.assertTrue(app.button(key='split_submit').disabled)
            self.assertFalse(app.exception)
        with patch('bank.seed.demo_path', return_value=self.store.path):
            app = AppTest.from_file(str(root / 'app/main.py'))
            app.session_state['action_flow'] = other
            app.run()
            self.assertTrue(app.button(key='example_0').disabled)
            self.assertFalse(app.exception)
