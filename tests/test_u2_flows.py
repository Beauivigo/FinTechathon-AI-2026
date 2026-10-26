"""U2: session expiry, original-operation recovery, TOTP failures and rebind.

Temporary bank only.  Reuses the deterministic TOTP stand-in installed by the
U1 test module when pyotp is not available in the isolated runtime.
"""
import secrets
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tests.test_u1_setup import pyotp, seal, unseal
from shared import ServiceError
from bank.store import Store
from bank.seed import initialize
from bank.auth import AuthService, digest
from bank.gateway import BankGateway
from agent.controller import Conversation

ROOT = Path(__file__).resolve().parents[1]
USERNAME = 'tester'


class U2FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'bank.sqlite3')
        initialize(self.store)
        self.ts = datetime(2027, 1, 2, 3, 4, tzinfo=timezone.utc).timestamp()
        self.password = 'T' + secrets.token_hex(16)
        self.auth = AuthService(self.store, clock=lambda: self.ts, seal=seal, unseal=unseal)
        self.codes = self.auth.bootstrap(USERNAME, self.password)
        self.token = self.auth.login(USERNAME, self.password)
        self.otp = pyotp.parse_uri(self.auth.enrollment(self.token))
        self.auth.finish_enrollment(self.token, self.otp.at(self.ts))
        self.ts += 30

    def gateway(self, token=None):
        return BankGateway(self.store, auth=self.auth, session_token=token or self.token,
                           clock=lambda: datetime.fromtimestamp(self.ts, timezone.utc))

    def pending_transfer(self):
        flow = Conversation(self.gateway(), principal_id='demo_user_a')
        result = flow.start_action('transfer.create', dict(
            account_id='demo_account_a', payee_id='demo_payee_li', amount_minor=120000))
        self.assertEqual(result['status'], 'awaiting_verification')
        self.assertEqual(result['next_action']['methods'], ['totp'])
        return flow

    def count(self, table):
        with self.store.connection() as c:
            return c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def test_expired_login_preserves_original_operation_and_requires_explicit_reverify(self):
        flow = self.pending_transfer()
        key = flow.request['idempotency_key']
        with self.store.connection(write=True) as c:
            c.execute('UPDATE auth_sessions SET expires_at=? WHERE token_hash=?', (self.ts - 1, digest(self.token)))
        self.assertEqual(self.auth.session_status(self.token), 'expired')
        with self.assertRaises(ServiceError):
            self.gateway().get_context(principal_id='demo_user_a')
        token2 = self.auth.login(USERNAME, self.password)
        fresh = Conversation(self.gateway(token2), principal_id='demo_user_a')
        self.assertEqual(self.count('transfers'), 0)
        fresh.recover(action='transfer.create', idempotency_key=key)
        self.assertEqual(fresh.result['status'], 'awaiting_verification')
        self.assertEqual(self.count('transfers'), 0)
        result = fresh.decide(True, totp_code=self.otp.at(self.ts))
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(self.count('transfers'), 1)
        # Repeating the already-consumed decision cannot pay twice.
        fresh.decide(True, totp_code=self.otp.at(self.ts))
        self.assertEqual(self.count('transfers'), 1)

    def test_expired_challenge_after_relogin_is_terminal_without_payment(self):
        flow = self.pending_transfer()
        key = flow.request['idempotency_key']
        self.ts += 3700
        token2 = self.auth.login(USERNAME, self.password)
        fresh = Conversation(self.gateway(token2), principal_id='demo_user_a')
        fresh.recover(action='transfer.create', idempotency_key=key)
        self.assertEqual(fresh.result['status'], 'awaiting_verification')
        with self.assertRaises(ServiceError) as expired:
            fresh.decide(True, totp_code=self.otp.at(self.ts))
        self.assertEqual(expired.exception.code, 'AUTH_EXPIRED')
        fresh.recover()
        self.assertEqual(fresh.result['status'], 'failed')
        self.assertEqual(fresh.result['error']['code'], 'AUTH_EXPIRED')
        self.assertEqual(self.count('transfers'), 0)

    def test_recovery_does_not_approve_payment_and_rebind_needs_new_verification(self):
        flow = self.pending_transfer()
        key = flow.request['idempotency_key']
        self.auth.recover(USERNAME, self.password, self.codes[0])
        with self.assertRaises(ServiceError):
            self.auth.principal(self.token)
        self.assertEqual(self.count('transfers'), 0)
        self.assertEqual(self.count('operations'), 1)
        token2 = self.auth.login(USERNAME, self.password)
        self.assertFalse(self.auth.account_status()['bound'])
        otp2 = pyotp.parse_uri(self.auth.enrollment(token2))
        self.auth.finish_enrollment(token2, otp2.at(self.ts))
        self.ts += 30
        fresh = Conversation(self.gateway(token2), principal_id='demo_user_a')
        fresh.recover(action='transfer.create', idempotency_key=key)
        self.assertEqual(fresh.result['status'], 'awaiting_verification')
        self.assertEqual(self.count('transfers'), 0)
        self.assertEqual(fresh.decide(True, totp_code=otp2.at(self.ts))['status'], 'succeeded')
        self.assertEqual(self.count('transfers'), 1)

    def test_wrong_codes_and_five_failures_leave_no_payment(self):
        flow = self.pending_transfer()
        challenge = flow.result['next_action']['challenge_id']
        for _ in range(4):
            with self.assertRaises(ServiceError) as wrong:
                self.gateway().issue_totp_proof(challenge, 'abcdef', principal_id='demo_user_a')
            self.assertEqual(wrong.exception.code, 'AUTH_INVALID')
        with self.assertRaises(ServiceError):
            self.gateway().issue_totp_proof(challenge, 'abcdef', principal_id='demo_user_a')
        fresh = Conversation(self.gateway(), principal_id='demo_user_a')
        fresh.recover(action=flow.request['action'], idempotency_key=flow.request['idempotency_key'])
        self.assertEqual(fresh.result['status'], 'blocked')
        self.assertEqual(fresh.result['error']['code'], 'SAFETY_LOCKED')
        self.assertEqual(self.count('transfers'), 0)

    def test_password_and_recovery_codes_are_not_stored_plaintext(self):
        self.pending_transfer()
        with self.store.connection(write=True) as c:
            c.execute('UPDATE auth_sessions SET expires_at=? WHERE token_hash=?', (self.ts - 1, digest(self.token)))
        self.auth.login(USERNAME, self.password)
        raw = self.store.path.read_bytes()
        self.assertNotIn(self.password.encode(), raw)
        self.assertNotIn(self.codes[1].encode(), raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
