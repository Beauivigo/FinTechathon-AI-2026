"""P4：客户端观察状态与持久化业务状态分离，恢复全程保留原键。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent.controller import Conversation
from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import adapt_result, ServiceError

USER = 'demo_user_a'


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'recovery.sqlite3')
        initialize(self.store)
        self.now = datetime(2026, 10, 13, 8, tzinfo=timezone.utc)
        self.gateway = BankGateway(self.store, clock=lambda: self.now)
        self.chat = Conversation(self.gateway, principal_id=USER)

    def count(self):
        with self.store.connection() as c:
            return c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0]

    def test_lost_execute_reply_recovers_pending_without_illegal_unknown_transition(self):
        execute = self.gateway.execute
        def lost(request, **kw):
            result = execute(request, **kw)
            if request['action'] == 'transfer.create':
                raise TimeoutError('lost')
            return result
        with patch.object(self.gateway, 'execute', side_effect=lost):
            self.chat.submit('给李明转200元')
        key = self.chat.request['idempotency_key']
        self.assertTrue(self.chat.needs_reconciliation)
        self.assertIsNone(self.chat.backend_result)
        self.assertEqual(self.chat.result['status'], 'unknown')
        self.chat.recover()
        self.assertEqual(self.chat.result['status'], 'awaiting_confirmation')
        self.assertFalse(self.chat.needs_reconciliation)
        self.assertEqual(self.chat.request['idempotency_key'], key)
        self.assertEqual(self.count(), 0)
        self.chat.decide(True)
        self.assertEqual(self.count(), 1)

    def test_lost_authorize_reply_recovers_success_and_no_second_debit(self):
        self.chat.submit('给李明转200元')
        authorize = self.gateway.authorize
        def lost(payload, **kw):
            authorize(payload, **kw)
            raise TimeoutError('lost')
        with patch.object(self.gateway, 'authorize', side_effect=lost):
            self.chat.decide(True)
        self.assertEqual(self.chat.backend_result['status'], 'awaiting_confirmation')
        self.assertEqual(self.chat.result['status'], 'unknown')
        self.chat.recover()
        self.assertEqual(self.chat.result['status'], 'succeeded')
        self.assertFalse(self.chat.blocked)
        self.chat.decide(True)
        self.assertEqual(self.count(), 1)

    def test_restart_lists_pending_and_blocks_new_transfer_until_recovered(self):
        self.chat.submit('给李明转200元')
        key = self.chat.request['idempotency_key']
        chat = Conversation(BankGateway(Store(self.store.path)), principal_id=USER)
        self.assertTrue(chat.blocked)
        chat.submit('给李明转500元')
        self.assertIsNone(chat.request)
        chat.recover(action='transfer.create', idempotency_key=key)
        self.assertEqual(chat.result['status'], 'awaiting_confirmation')
        self.assertEqual(self.count(), 0)

    def test_restart_restores_completed_operation_from_original_request(self):
        self.chat.submit('给李明转200元')
        self.chat.decide(True)
        original = deepcopy(self.chat.request)
        chat = Conversation(BankGateway(Store(self.store.path)), principal_id=USER)
        chat.recover(action='transfer.create', idempotency_key=original['idempotency_key'])
        self.assertEqual(chat.request, original)
        self.assertEqual(chat.result['status'], 'succeeded')
        self.assertEqual(self.count(), 1)

    def test_not_found_keeps_unknown_and_original_key(self):
        execute = self.gateway.execute
        def unavailable(request, **kw):
            if request['action'] == 'transfer.create':
                raise TimeoutError('before reaching server')
            return execute(request, **kw)
        with patch.object(self.gateway, 'execute', side_effect=unavailable):
            self.chat.submit('给李明转200元')
        original = deepcopy(self.chat.request)
        message = self.chat.recover()
        self.assertIn('不能据此认定未扣款', message)
        self.assertEqual(self.chat.result['status'], 'unknown')
        self.chat.submit('给李明转200元')
        self.assertEqual(self.chat.request, original)

    def test_recovery_rejects_mismatched_content_under_same_key(self):
        self.chat.submit('给李明转200元')
        execute = self.gateway.execute
        def wrong(request, **kw):
            result = execute(request, **kw)
            if request['action'] == 'operation.get':
                result['data']['operation_request']['params']['amount_minor'] = 30000
            return result
        with patch.object(self.gateway, 'execute', side_effect=wrong):
            self.chat.recover()
        self.assertEqual(self.chat.request['params']['amount_minor'], 20000)
        self.assertEqual(self.chat.result['status'], 'unknown')
        self.assertTrue(self.chat.blocked)

    def test_invalid_proof_is_not_misreported_as_terminal_failure(self):
        self.chat.submit('给李明转1200元')
        with patch.object(self.gateway, 'issue_demo_proof', return_value='wrong'):
            self.chat.decide(True, simulate_verification=True)
        self.assertEqual(self.chat.result['status'], 'unknown')
        self.chat.recover()
        self.assertEqual(self.chat.result['status'], 'awaiting_verification')
        self.chat.decide(True, simulate_verification=True)
        self.assertEqual(self.chat.result['status'], 'succeeded')

    def test_expired_proof_issuance_is_recovered_as_failed(self):
        self.chat.submit('给李明转1200元')
        self.now += timedelta(minutes=10)
        self.chat.decide(True, simulate_verification=True)
        self.assertEqual(self.chat.result['status'], 'unknown')
        self.chat.recover()
        self.assertEqual(self.chat.result['error']['code'], 'AUTH_EXPIRED')
        self.assertFalse(self.chat.blocked)

    def test_backend_unknown_stays_blocked_after_query(self):
        self.chat.submit('给李明转200元')
        unknown = adapt_result(self.chat.request, dict(status='unknown', error=dict(code='RESULT_UNKNOWN', message='待核实')), source='sandbox')
        with self.store.connection(write=True) as c:
            c.execute('UPDATE operations SET result_json=?', (json.dumps(unknown),))
        self.chat.recover()
        self.assertFalse(self.chat.needs_reconciliation)
        self.assertEqual(self.chat.result['status'], 'unknown')
        self.assertTrue(self.chat.blocked)
        self.chat.decide(True)
        self.assertEqual(self.count(), 0)

    def test_new_request_id_retry_recovers_original_before_authorization(self):
        self.chat.submit('给李明转200元')
        original = deepcopy(self.chat.request)
        self.chat.request = original | dict(request_id='retry', trace_id='retry_trace')
        self.chat.result = self.gateway.execute(self.chat.request, principal_id=USER)
        self.chat.backend_result = deepcopy(self.chat.result)
        self.chat.recover()
        self.assertEqual(self.chat.request, original)
        self.chat.decide(True)
        self.assertEqual(self.chat.result['status'], 'succeeded')

    def test_cannot_switch_away_from_unresolved_current_operation(self):
        self.chat.submit('给李明转200元')
        with self.assertRaises(ValueError):
            self.chat.recover(action='transfer.create', idempotency_key='other')
        self.assertEqual(self.chat.result['status'], 'awaiting_confirmation')

    def test_recovery_index_is_scoped_and_keeps_old_pending(self):
        self.chat.submit('给李明转200元')
        original = self.chat.request
        for i in range(22):
            request = deepcopy(original)
            request.update(idempotency_key=f'other_{i}', request_id=f'request_{i}')
            pending = self.gateway.execute(request, principal_id=USER)
            n = pending['next_action']
            self.gateway.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],
                operation_digest=n['operation_digest'], decision='reject'), principal_id=USER)
        operations = self.gateway.list_operations(principal_id=USER)
        self.assertEqual(len(operations), 21)
        self.assertEqual(operations[0]['idempotency_key'], original['idempotency_key'])
        self.assertEqual(self.gateway.list_operations(principal_id='demo_user_li'), [])

    def test_query_outage_never_clears_original_request(self):
        self.chat.submit('给李明转200元')
        original = deepcopy(self.chat.request)
        with patch.object(self.gateway, 'execute', side_effect=TimeoutError):
            self.chat.recover()
        self.assertEqual(self.chat.request, original)
        self.assertEqual(self.chat.result['status'], 'unknown')
        self.chat.recover()
        self.assertEqual(self.chat.result['status'], 'awaiting_confirmation')


if __name__ == '__main__':
    unittest.main()
