from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import unittest

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import adapt_request, ServiceError
from tests.ui_support import AppTest


class CardTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'bank.db')
        initialize(self.store)
        self.g = BankGateway(self.store)

    def request(self, action, card='CARD-E001'):
        return adapt_request(action, dict(card_id=card), request_id='r', trace_id='t', idempotency_key=uuid4().hex)

    def start(self, request, principal='demo_user_a'):
        return self.g.execute(request, principal_id=principal)

    def finish(self, pending, decision='approve', proof=True):
        n = pending['next_action']
        p = dict(schema_version='1.1', challenge_id=n['challenge_id'],
                 operation_digest=n['operation_digest'], decision=decision)
        if proof and decision == 'approve':
            p['proof_token'] = self.g.issue_demo_proof(n['challenge_id'], principal_id='demo_user_a')
        return self.g.authorize(p, principal_id='demo_user_a')

    def state(self, card='CARD-E001'):
        return next(c['card_state'] for c in self.g.get_context(principal_id='demo_user_a')['cards'] if c['card_id'] == card)

    def test_four_actions_and_lost_cannot_unfreeze(self):
        for action, expected in [('card.freeze','frozen'), ('card.unfreeze','active'),
                                 ('card.report_loss','lost'), ('card.release_loss','frozen')]:
            req = self.request(action)
            pending = self.start(req)
            self.assertEqual(pending['status'], 'awaiting_verification')
            self.g = BankGateway(self.store)
            result = self.finish(pending)
            self.assertEqual(result['status'], 'succeeded')
            self.assertEqual(self.state(), expected)
            self.assertEqual(self.start(req), result)
            if expected == 'lost':
                self.assertEqual(self.start(self.request('card.unfreeze'))['error']['code'], 'INVALID_STATE')
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ledger_entries').fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0], 200000)

    def test_e_material_original_paths(self):
        for action, card, expected in [('card.unfreeze','CARD-E002','active'),
            ('card.unfreeze','CARD-E003',None), ('card.release_loss','CARD-E003','frozen'),
            ('card.report_loss','CARD-E001','lost'), ('card.report_loss','CARD-E003','lost')]:
            pending = self.start(self.request(action, card))
            if expected is None:
                self.assertEqual(pending['error']['code'],'INVALID_STATE')
            else:
                self.assertEqual(self.finish(pending)['status'],'succeeded')
                self.assertEqual(self.state(card),expected)

    def test_ownership_reject_missing_proof_and_conflict(self):
        req = self.request('card.freeze')
        self.assertEqual(self.start(req, 'demo_user_li')['error']['code'], 'FORBIDDEN')
        pending = self.start(req)
        with self.assertRaises(ServiceError):
            self.finish(pending, proof=False)
        self.assertEqual(self.state(), 'active')
        changed = req | dict(params=dict(card_id='CARD-E002'))
        self.assertEqual(self.start(changed)['error']['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.finish(pending, decision='reject')['status'], 'cancelled')
        self.assertEqual(self.state(), 'active')

    def test_concurrent_state_change_invalidates_old_confirmation(self):
        older = self.start(self.request('card.freeze'))
        self.finish(self.start(self.request('card.freeze')))
        self.finish(self.start(self.request('card.unfreeze')))
        self.assertEqual(self.state(), 'active')
        self.assertEqual(self.finish(older)['error']['code'], 'AUTH_INVALID')

    def test_audit_failure_rolls_back_card(self):
        pending = self.start(self.request('card.freeze'))
        original = self.g._audit
        def fail(*args, **kwargs):
            if args[3] == 'authorization_consumed':
                raise ServiceError('UPSTREAM_ERROR', 'injected')
            return original(*args, **kwargs)
        with patch.object(self.g, '_audit', side_effect=fail):
            self.assertEqual(self.finish(pending)['status'], 'failed')
        self.assertEqual(self.state(), 'active')

    def test_existing_v3_database_migrates_preserving_state(self):
        with self.store.connection(write=True) as c:
            c.execute('DROP TABLE cards')
            c.execute("DELETE FROM metadata WHERE key='cards_seed_version'")
            c.execute('ALTER TABLE challenges DROP COLUMN resource_binding_json')
            c.execute('PRAGMA user_version=3')
        initialize(self.store)
        self.finish(self.start(self.request('card.freeze')))
        initialize(self.store)
        self.assertEqual(self.state(), 'frozen')

    def test_page_and_refresh_recovery_and_transfer_page(self):
        page = str(Path(__file__).resolve().parents[1] / 'app/pages/3_卡片管理.py')
        with patch('app.demo.gateway', return_value=self.g):
            app = AppTest.from_file(page).run()
            app.button(key='card_submit').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(self.state(), 'active')
            app = AppTest.from_file(page).run()
            app.button(key='action_recover').click().run()
            app.button(key='action_approve').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(self.state(), 'frozen')
        with patch('bank.seed.demo_path', return_value=self.store.path):
            main = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app/main.py')).run()
            self.assertFalse(main.exception)
