"""P3 公共入口验收：全部使用临时库和模拟身份，不触碰真实资金。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store, STORAGE_VERSION
from shared import adapt_request, validate_result, ServiceError

USER = 'demo_user_a'


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'bank.sqlite3')
        initialize(self.store)
        self.now = datetime(2026, 10, 13, 8, tzinfo=timezone.utc)
        self.gateway = self.restart()

    def restart(self):
        self.gateway = BankGateway(self.store, clock=lambda: self.now)
        return self.gateway

    def request(self, amount=20000, key=None, **params):
        return adapt_request('transfer.create', dict(account_id='demo_account_a',
            payee_id='demo_payee_li', amount_minor=amount, **params), request_id='req_'+uuid4().hex,
            trace_id='trace_'+uuid4().hex, idempotency_key=key or 'op_'+uuid4().hex)

    def start(self, request=None):
        request = request or self.request()
        result = self.gateway.execute(request, principal_id=USER)
        validate_result(request, result)
        return result

    def payload(self, result, decision='approve', **extra):
        n = result['next_action']
        return dict(schema_version='1.1', challenge_id=n['challenge_id'],
            operation_digest=n['operation_digest'], decision=decision, **extra)

    def approve(self, result, **extra):
        return self.gateway.authorize(self.payload(result, **extra), principal_id=USER)

    def proof(self, result):
        return self.gateway.issue_demo_proof(result['next_action']['challenge_id'], principal_id=USER)

    def saved(self, request):
        q = adapt_request('operation.get', dict(original_action='transfer.create',
            original_idempotency_key=request['idempotency_key']), request_id='query', trace_id='query_trace')
        return self.gateway.execute(q, principal_id=USER)['data']['operation_result']

    def count(self, table):
        with self.store.connection() as c:
            return c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def balance(self):
        with self.store.connection() as c:
            return c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0]

    def assert_error(self, code, callback):
        with self.assertRaises(ServiceError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_pending_survives_restart_and_never_moves_money(self):
        req = self.request()
        result = self.start(req)
        self.restart()
        self.assertEqual(self.saved(req), result)
        self.assertEqual(self.start(req), result)
        self.assertEqual(self.balance(), 200000)
        for table in ('transfers', 'ledger_entries', 'fund_postings'):
            self.assertEqual(self.count(table), 0)
        self.assertEqual(self.count('challenges'), 1)

    def test_confirm_commits_pair_and_consumes_challenge_once(self):
        req = self.request()
        pending = self.start(req)
        result = self.approve(pending)
        validate_result(req, result)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['source'], 'sandbox')
        self.assertEqual(self.approve(pending), result)
        self.assertEqual(self.balance(), 180000)
        self.assertEqual(self.count('ledger_entries'), 2)
        self.assertEqual(self.count('fund_postings'), 2)
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT state FROM challenges').fetchone()[0], 'used')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_events WHERE outcome='authorization_consumed'").fetchone()[0], 1)

    def test_replay_reassociates_response_without_changing_original(self):
        req = self.request()
        pending = self.start(req)
        retry = req | dict(request_id='retry', trace_id='retry_trace')
        self.assertEqual(self.start(retry)['request_id'], 'retry')
        self.assertEqual(self.saved(req)['request_id'], req['request_id'])
        done = self.approve(pending)
        replay = self.start(retry)
        self.assertEqual(replay['request_id'], 'retry')
        self.assertEqual(replay['data'], done['data'])
        self.assertEqual(self.count('transfers'), 1)

    def test_same_key_different_amount_rejected_even_after_success(self):
        req = self.request()
        self.approve(self.start(req))
        changed = deepcopy(req)
        changed['params']['amount_minor'] += 1
        self.assertEqual(self.start(changed)['error']['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.saved(req)['status'], 'succeeded')

    def test_threshold_includes_current_amount_and_equality_is_confirm(self):
        self.assertEqual(self.start(self.request(100000))['status'], 'awaiting_confirmation')
        self.assertEqual(self.start(self.request(100001))['status'], 'awaiting_verification')
        self.approve(self.start(self.request(80000)))
        self.assertEqual(self.start(self.request(20000))['status'], 'awaiting_confirmation')
        self.assertEqual(self.start(self.request(20001))['status'], 'awaiting_verification')

    def test_plain_confirmation_cannot_satisfy_verification(self):
        result = self.start(self.request(100001))
        self.assert_error('AUTH_INVALID', lambda: self.approve(result))
        self.assertEqual(self.balance(), 200000)
        token = self.proof(result)
        self.assertEqual(self.approve(result, proof_token=token)['status'], 'succeeded')

    def test_proof_is_hashed_not_logged_and_survives_restart(self):
        result = self.start(self.request(100001))
        token = self.proof(result)
        with self.store.connection() as c:
            dump = '\n'.join(c.iterdump())
            self.assertNotIn(token, dump)
            self.assertEqual(len(c.execute('SELECT proof_digest FROM challenges').fetchone()[0]), 64)
        self.restart()
        self.assertEqual(self.approve(result, proof_token=token)['status'], 'succeeded')
        with self.store.connection() as c:
            self.assertIsNone(c.execute('SELECT proof_digest FROM challenges').fetchone()[0])

    def test_proof_is_bound_to_challenge_and_reissue_invalidates_old_token(self):
        first = self.start(self.request(100001))
        second = self.start(self.request(100001))
        token = self.proof(first)
        self.assert_error('AUTH_INVALID', lambda: self.approve(second, proof_token=token))
        replacement = self.proof(first)
        self.assert_error('AUTH_INVALID', lambda: self.approve(first, proof_token=token))
        self.assertEqual(self.approve(first, proof_token=replacement)['status'], 'succeeded')

    def test_five_invalid_attempts_lock_persistently_despite_proof_reissue(self):
        req = self.request(100001)
        result = self.start(req)
        for _ in range(4):
            self.assert_error('AUTH_INVALID', lambda: self.approve(result, proof_token='wrong'))
            self.restart()
            self.proof(result)
        locked = self.approve(result, proof_token='wrong')
        self.assertEqual(locked['status'], 'blocked')
        self.assertEqual(locked['error']['code'], 'SAFETY_LOCKED')
        self.assertEqual(self.saved(req), locked)
        self.assert_error('AUTH_INVALID', lambda: self.proof(result))
        self.assertEqual(self.approve(result), locked)
        self.assertEqual(self.balance(), 200000)

    def test_other_user_cannot_authorize_issue_proof_or_lock_owner(self):
        result = self.start(self.request(100001))
        for _ in range(6):
            self.assert_error('AUTH_INVALID', lambda: self.gateway.authorize(self.payload(result), principal_id='demo_user_li'))
        self.assert_error('AUTH_INVALID', lambda: self.gateway.issue_demo_proof(
            result['next_action']['challenge_id'], principal_id='demo_user_li'))
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT failed_attempts FROM challenges').fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_events WHERE principal_id='demo_user_li' AND outcome='authorization_invalid'").fetchone()[0], 6)
        self.assertEqual(self.approve(result, proof_token=self.proof(result))['status'], 'succeeded')

    def test_wrong_digest_cannot_change_pending_or_completed_operation(self):
        req = self.request()
        pending = self.start(req)
        wrong = self.payload(pending) | dict(operation_digest='0'*64)
        self.assert_error('AUTH_INVALID', lambda: self.gateway.authorize(wrong, principal_id=USER))
        self.assertEqual(self.saved(req), pending)
        done = self.approve(pending)
        self.assert_error('AUTH_INVALID', lambda: self.gateway.authorize(wrong, principal_id=USER))
        self.assertEqual(self.saved(req), done)

    def test_expiry_exact_boundary_fails_without_silent_renewal(self):
        req = self.request()
        pending = self.start(req)
        self.now += timedelta(minutes=10)
        result = self.approve(pending)
        self.assertEqual(result['error']['code'], 'AUTH_EXPIRED')
        self.assertEqual(self.start(req), result)
        self.assertEqual(self.count('challenges'), 1)
        self.assertEqual(self.start(self.request())['status'], 'awaiting_confirmation')
        self.assertEqual(self.balance(), 200000)

    def test_proof_cannot_be_issued_after_expiry(self):
        req = self.request(100001)
        pending = self.start(req)
        self.now += timedelta(minutes=10)
        self.assert_error('AUTH_EXPIRED', lambda: self.proof(pending))
        self.assertEqual(self.saved(req)['error']['code'], 'AUTH_EXPIRED')

    def test_reject_is_terminal_and_never_pays(self):
        pending = self.start()
        result = self.gateway.authorize(self.payload(pending, 'reject'), principal_id=USER)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(self.approve(pending), result)
        self.assertEqual(self.balance(), 200000)

    def test_concurrent_confirmations_escalate_second_without_debit(self):
        self.approve(self.start(self.request(80000)))
        first, second = self.start(), self.start()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.approve, (first, second)))
        self.assertCountEqual([r['status'] for r in results], ['succeeded', 'awaiting_verification'])
        self.assertEqual(self.balance(), 100000)
        i = next(i for i, r in enumerate(results) if r['status'] == 'awaiting_verification')
        old, upgraded = (first, second)[i], results[i]
        self.assert_error('AUTH_INVALID', lambda: self.approve(old))
        self.assertEqual(self.approve(upgraded, proof_token=self.proof(upgraded))['status'], 'succeeded')
        self.assertEqual(self.balance(), 80000)

    def test_concurrent_retries_of_same_authorization_only_pay_once(self):
        pending = self.start()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.approve(pending), range(4)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(self.count('transfers'), 1)
        self.assertEqual(self.balance(), 180000)

    def test_concurrent_execute_creates_one_challenge(self):
        req = self.request()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.start(req), range(4)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(self.count('challenges'), 1)

    def test_day_is_shanghai_execution_day_and_verification_never_downgrades(self):
        self.now = datetime(2026, 10, 13, 15, 59, tzinfo=timezone.utc)
        self.approve(self.start(self.request(80000)))
        pending = self.start(self.request(30000))
        self.assertEqual(pending['status'], 'awaiting_verification')
        self.now += timedelta(minutes=1)
        self.assertEqual(self.start(self.request(100000))['status'], 'awaiting_confirmation')
        self.assert_error('AUTH_INVALID', lambda: self.approve(pending))
        self.approve(pending, proof_token=self.proof(pending))
        self.assertEqual(self.start(self.request(70001))['status'], 'awaiting_verification')

    def test_daily_total_spans_all_owned_accounts(self):
        self.approve(self.start(self.request(80000)))
        with self.store.connection(write=True) as c:
            c.execute("INSERT INTO accounts VALUES ('second','demo_user_a','第二账户','CNY',50000,50000,50000,0)")
        req = self.request(30000)
        req['params']['account_id'] = 'second'
        self.assertEqual(self.start(req)['status'], 'awaiting_verification')

    def test_payee_mapping_change_requires_new_confirmation(self):
        pending = self.start()
        with self.store.connection(write=True) as c:
            c.execute("UPDATE payees SET account_id='demo_account_wang_1' WHERE payee_id='demo_payee_li'")
        self.assertEqual(self.approve(pending)['error']['code'], 'AUTH_INVALID')
        self.assertEqual(self.balance(), 200000)

    def test_foreign_account_and_payee_fail_before_challenge(self):
        req = self.request()
        req['params']['account_id'] = 'demo_account_li'
        self.assertEqual(self.start(req)['error']['code'], 'FORBIDDEN')
        req = self.request()
        req['params']['payee_id'] = 'missing'
        self.assertEqual(self.start(req)['error']['code'], 'FORBIDDEN')
        self.assertEqual(self.count('challenges'), 0)

    def test_balance_is_rechecked_and_failure_is_audited(self):
        pending = self.start(self.request(200001))
        result = self.approve(pending, proof_token=self.proof(pending))
        self.assertEqual(result['error']['code'], 'INSUFFICIENT_FUNDS')
        self.assertEqual(self.balance(), 200000)
        self.assertEqual(self.count('ledger_entries'), 0)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_events WHERE outcome='failed'").fetchone()[0], 1)

    def test_credit_failure_rolls_back_debit_and_success_audit(self):
        req = self.request()
        pending = self.start(req)
        post = self.gateway.services.post_funds
        def fail_credit(transaction, command, **kw):
            if command['effect'] == 'credit':
                raise ServiceError('UPSTREAM_ERROR', 'injected')
            return post(transaction, command, **kw)
        with patch.object(self.gateway.services, 'post_funds', side_effect=fail_credit):
            result = self.approve(pending)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.saved(req), result)
        self.assertEqual(self.balance(), 200000)
        for table in ('transfers', 'ledger_entries', 'fund_postings'):
            self.assertEqual(self.count(table), 0)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_events WHERE outcome IN ('succeeded','authorization_consumed')").fetchone()[0], 0)

    def test_challenge_consumption_failure_rolls_back_funds(self):
        req = self.request()
        pending = self.start(req)
        with self.store.connection(write=True) as c:
            c.execute("""CREATE TRIGGER fail_consumption BEFORE INSERT ON audit_events
                WHEN NEW.outcome='authorization_consumed' BEGIN SELECT RAISE(ABORT,'injected'); END""")
        self.assertEqual(self.approve(pending)['status'], 'failed')
        self.assertEqual(self.balance(), 200000)
        self.assertEqual(self.count('transfers'), 0)

    def test_lost_commit_reply_preserves_success_for_operation_query(self):
        req = self.request()
        pending = self.start(req)
        original = self.store.connection
        @contextmanager
        def lost_reply(*, write=False):
            with original(write=write) as c:
                yield c
            if write:
                raise sqlite3.OperationalError('simulated lost commit reply')
        with patch.object(self.store, 'connection', lost_reply):
            self.assert_error('RESULT_UNKNOWN', lambda: self.approve(pending))
        self.restart()
        done = self.saved(req)
        self.assertEqual(done['status'], 'succeeded')
        self.assertEqual(self.approve(pending), done)
        self.assertEqual(self.count('transfers'), 1)

    def test_outer_rollback_restores_pending_and_unused_challenge(self):
        req = self.request()
        pending = self.start(req)
        original = self.store.connection
        @contextmanager
        def failed_commit(*, write=False):
            with original(write=write) as c:
                yield c
                if write:
                    raise sqlite3.OperationalError('simulated pre-commit failure')
        with patch.object(self.store, 'connection', failed_commit):
            self.assert_error('RESULT_UNKNOWN', lambda: self.approve(pending))
        self.assertEqual(self.saved(req), pending)
        self.assertEqual(self.balance(), 200000)
        self.assertEqual(self.count('transfers'), 0)
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT state FROM challenges').fetchone()[0], 'pending')
        self.assertEqual(self.approve(pending)['status'], 'succeeded')

    def test_v2_migration_adds_binding_without_changing_balance(self):
        with self.store.connection(write=True) as c:
            c.execute('ALTER TABLE challenges DROP COLUMN payee_account_id')
            c.execute('PRAGMA user_version=2')
        initialize(self.store)
        initialize(self.store)
        self.assertEqual(self.balance(), 200000)
        with self.store.connection() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], STORAGE_VERSION)
        self.assertEqual(self.approve(self.start())['status'], 'succeeded')

    def test_concurrent_balance_competition_never_overdraws(self):
        with self.store.connection(write=True) as c:
            c.execute("UPDATE accounts SET opening_balance_minor=100000,balance_minor=100000,available_minor=100000 WHERE account_id='demo_account_a'")
        pending = [self.start(self.request(60000)) for _ in range(2)]
        def finish(result):
            reply = self.approve(result)
            if reply['status'] == 'awaiting_verification':
                reply = self.approve(reply, proof_token=self.proof(reply))
            return reply
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(finish, pending))
        self.assertCountEqual([r['status'] for r in results], ['succeeded', 'failed'])
        failure = next(r for r in results if r['status'] == 'failed')
        self.assertEqual(failure['error']['code'], 'INSUFFICIENT_FUNDS')
        self.assertEqual(self.balance(), 40000)
        self.assertEqual(self.count('ledger_entries'), 2)

    def test_invalid_module_result_rolls_back_both_postings(self):
        from bank.transfer import handle
        for amount in (1, 20000):
            with self.subTest(amount=amount):
                req = self.request()
                pending = self.start(req)
                def bad_result(params, *, context):
                    handle(params, context=context)
                    return dict(status='succeeded', data=dict(transaction_id='bad', amount_minor=amount))
                with patch('bank.gateway.handle', side_effect=bad_result):
                    self.assertEqual(self.approve(pending)['status'], 'failed')
                self.assertEqual(self.balance(), 200000)
                self.assertEqual(self.count('ledger_entries'), 0)
                self.assertEqual(self.saved(req)['status'], 'failed')

    def test_existing_unknown_operation_is_never_executed_again(self):
        req = self.request()
        pending = self.start(req)
        unknown = self.gateway._error(req, 'RESULT_UNKNOWN', status='unknown')
        with self.store.connection(write=True) as c:
            c.execute('UPDATE operations SET result_json=?', (json.dumps(unknown),))
        self.assertEqual(self.start(req), unknown)
        self.assertEqual(self.approve(pending), unknown)
        self.assertEqual(self.count('transfers'), 0)

    def test_pending_without_legacy_payee_binding_fails_closed(self):
        pending = self.start()
        with self.store.connection(write=True) as c:
            c.execute('UPDATE challenges SET payee_account_id=NULL')
        self.assertEqual(self.approve(pending)['error']['code'], 'AUTH_INVALID')
        self.assertEqual(self.balance(), 200000)

    def test_lost_pending_reply_recovers_same_challenge(self):
        req = self.request()
        original = self.store.connection
        @contextmanager
        def lost_reply(*, write=False):
            with original(write=write) as c:
                yield c
            if write:
                raise sqlite3.OperationalError('simulated response loss')
        with patch.object(self.store, 'connection', lost_reply):
            self.assertEqual(self.start(req)['status'], 'unknown')
        self.restart()
        self.assertEqual(self.start(req), self.saved(req))
        self.assertEqual(self.saved(req)['status'], 'awaiting_confirmation')
        self.assertEqual(self.count('challenges'), 1)
        self.assertEqual(self.balance(), 200000)


if __name__ == '__main__':
    unittest.main()
