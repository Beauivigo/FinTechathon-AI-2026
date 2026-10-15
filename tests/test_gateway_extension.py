"""公共授权扩展的事务验收，业务替身只写临时库中的标记。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import ServiceError, adapt_request, adapt_result


class ProbeGateway(BankGateway):
    write_actions = BankGateway.write_actions | {'card.freeze', 'transfer.split'}
    fail = False
    binding = 'original-resource'

    def _binding(self, c, request, principal):
        return self.binding

    def _commit_action(self, c, request, previous, principal, now):
        c.execute("INSERT INTO metadata VALUES ('probe','applied')")
        if self.fail:
            raise ServiceError('UPSTREAM_ERROR', '模拟业务失败')
        data = (dict(card_id='card_probe', card_state='frozen') if request['action'] == 'card.freeze'
                else dict(collection_id='collection_probe', shares=[dict(payee_id='p', amount_minor=1)]))
        result = adapt_result(request, dict(status='succeeded', data=data), source='sandbox')
        self._save(c, request, result, principal, previous)
        return result


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'test.db')
        initialize(self.store)
        self.now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        self.g = ProbeGateway(self.store, clock=lambda: self.now)

    def start(self, action='card.freeze'):
        params = dict(card_id='card_probe') if action == 'card.freeze' else dict(
            account_id='demo_account_a', payee_ids=['p'], amount_minor=1)
        self.req = adapt_request(action, params, request_id='r', trace_id='t', idempotency_key='key')
        result = self.g.execute(self.req, principal_id='demo_user_a')
        n = result['next_action']
        self.payload = dict(schema_version='1.1', challenge_id=n['challenge_id'],
                            operation_digest=n['operation_digest'], decision='approve')
        return result

    def approve(self, proof=False):
        if proof:
            self.payload['proof_token'] = self.g.issue_demo_proof(
                self.payload['challenge_id'], principal_id='demo_user_a')
        return self.g.authorize(self.payload, principal_id='demo_user_a')

    def test_red_gate_replay_and_atomic_rollback(self):
        self.assertEqual(self.start()['status'], 'awaiting_verification')
        with self.assertRaises(ServiceError):
            self.approve()
        self.g.fail = True
        self.assertEqual(self.approve(True)['status'], 'failed')
        with self.store.connection() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM metadata WHERE key='probe'").fetchone())
        self.g.fail = False
        self.assertEqual(self.g.execute(self.req, principal_id='demo_user_a')['status'], 'failed')

    def test_yellow_confirmation_survives_restart(self):
        self.assertEqual(self.start('transfer.split')['status'], 'awaiting_confirmation')
        self.g = ProbeGateway(self.store, clock=lambda: self.now)
        result = self.approve()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(self.approve(), result)

    def test_resource_changed_and_expiry_fail_closed(self):
        self.start()
        self.g.binding = 'changed'
        self.assertEqual(self.approve(True)['error']['code'], 'AUTH_INVALID')
        self.g.binding = 'original-resource'
        self.start('transfer.split')
        self.now += timedelta(minutes=10)
        self.assertEqual(self.approve()['error']['code'], 'AUTH_EXPIRED')
