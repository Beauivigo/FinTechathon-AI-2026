"""U1: in-app first-run setup, recovery custody, binding and local-only guard.

Temporary bank only; no runtime data, real credentials or model calls.  The
password and recovery codes are generated per test.  These final acceptance checks require the installed pyotp implementation;
missing dependencies are reported instead of replacing authentication code.
"""
import base64
import hashlib
import hmac
import os
import secrets
import struct
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

import pyotp

from shared import ServiceError
from bank.store import Store, STORAGE_VERSION
from bank.seed import initialize
from bank.auth import AuthService

ROOT = Path(__file__).resolve().parents[1]
SEAL_PREFIX = b'stage-test-sealed:'


def seal(raw):
    return SEAL_PREFIX + raw


def unseal(raw):
    raw = bytes(raw)
    if not raw.startswith(SEAL_PREFIX):
        raise ValueError('bad test seal')
    return raw[len(SEAL_PREFIX):]


class U1SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'bank.sqlite3')
        initialize(self.store)
        self.ts = 1800000000.0
        self.username = 'local-user'
        self.password = 'T' + secrets.token_hex(16)
        self.auth = self.make_auth()

    def make_auth(self):
        return AuthService(self.store, clock=lambda: self.ts, seal=seal, unseal=unseal)

    def begin(self):
        return self.auth.begin_setup(self.username, self.password, self.password)

    def create_bound(self):
        result = self.begin()
        token, codes = result['token'], result['codes']
        self.auth.confirm_recovery_saved(token)
        otp = pyotp.parse_uri(self.auth.enrollment(token))
        self.auth.finish_enrollment(token, otp.at(self.ts))
        self.ts += 30
        return token, codes, otp

    def test_first_run_confirmation_validation_and_sealed_package(self):
        with self.assertRaises(ServiceError) as mismatch:
            self.auth.begin_setup(self.username, self.password, self.password + 'x')
        self.assertEqual(mismatch.exception.code, 'INVALID_REQUEST')
        self.assertFalse(self.auth.account_status()['initialized'])
        with self.assertRaises(ServiceError):
            self.auth.begin_setup(self.username, 'too-short', 'too-short')
        result = self.begin()
        self.assertEqual(result['recovery_remaining'], 8)
        self.assertEqual(len(set(result['codes'])), 8)
        status = self.auth.account_status()
        self.assertEqual(status['setup_state'], 'recovery_pending')
        self.assertFalse(status['bound'])
        with self.store.connection() as c:
            package = bytes(c.execute('SELECT setup_package FROM auth_users').fetchone()[0])
            self.assertTrue(package)
            self.assertNotIn(self.password.encode(), package)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM auth_recovery').fetchone()[0], 8)

    def test_concurrent_and_duplicate_setup_create_one_account(self):
        barrier = threading.Barrier(2)
        outcomes = []

        def attempt(index):
            auth = self.make_auth()
            barrier.wait()
            try:
                outcomes.append(('ok', auth.begin_setup('user-%d' % index, self.password, self.password)))
            except ServiceError as exc:
                outcomes.append(('error', exc.code))

        threads = [threading.Thread(target=attempt, args=(i,)) for i in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(sorted(kind for kind, _ in outcomes), ['error', 'ok'])
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM auth_users').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM auth_recovery').fetchone()[0], 8)
        with self.assertRaises(ServiceError) as duplicate:
            self.begin()
        self.assertEqual(duplicate.exception.code, 'INVALID_STATE')
        self.assertEqual(self.auth.account_status()['setup_state'], 'recovery_pending')

    def test_refresh_resumes_same_recovery_codes_then_binds_phone(self):
        result = self.begin()
        token, codes = result['token'], result['codes']
        fresh = self.make_auth()
        self.assertEqual(fresh.pending_recovery(token), codes)
        self.assertEqual(fresh.session_status(token), 'active')
        with self.assertRaises(ServiceError):
            fresh.enrollment(token)
        fresh.confirm_recovery_saved(token)
        self.assertEqual(fresh.pending_recovery(token), [])
        self.assertEqual(fresh.account_status()['setup_state'], 'binding_pending')
        otp = pyotp.parse_uri(fresh.enrollment(token))
        with self.assertRaises(ServiceError):
            fresh.finish_enrollment(token, 'abcdef')
        self.assertFalse(fresh.account_status()['bound'])
        fresh.finish_enrollment(token, otp.at(self.ts))
        status = fresh.account_status()
        self.assertTrue(status['bound'])
        self.assertEqual(status['setup_state'], 'active')
        self.assertEqual(fresh.principal(token), 'demo_user_a')

    def test_recovery_code_single_use_does_not_approve_payment(self):
        token, codes, otp = self.create_bound()
        other = self.auth.login(self.username, self.password)
        self.auth.recover(self.username, self.password, codes[0])
        for revoked in (token, other):
            with self.assertRaises(ServiceError):
                self.auth.principal(revoked)
        self.assertFalse(self.auth.account_status()['bound'])
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM operations').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0], 0)
        new_token = self.auth.login(self.username, self.password)
        self.assertEqual(self.auth.remaining_recovery(new_token), 7)
        with self.assertRaises(ServiceError):
            self.auth.recover(self.username, self.password, codes[0])

    def test_login_lock_and_unbound_session_are_reported(self):
        self.begin()
        wrong = self.password + 'x'
        for _ in range(4):
            self.assertEqual(self.auth.login_detail(self.username, wrong)[:2], (None, 'INVALID'))
        token, error, retry = self.auth.login_detail(self.username, wrong)
        self.assertIsNone(token)
        self.assertEqual(error, 'LOCKED')
        self.assertGreater(retry, 0)
        with self.assertRaises(ServiceError):
            self.auth.login(self.username, self.password)

    def test_legacy_bootstrap_identity_cannot_be_overwritten(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name) / 'bank.sqlite3')
        initialize(store)
        auth = AuthService(store, clock=lambda: self.ts, seal=seal, unseal=unseal)
        self.assertEqual(len(auth.bootstrap(self.username, self.password)), 8)
        self.assertEqual(auth.account_status()['setup_state'], 'active')
        with self.assertRaises(ServiceError):
            auth.begin_setup('other-user', self.password, self.password)
        with store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM auth_users').fetchone()[0], 1)

    def test_local_guard_and_schema_columns(self):
        from bank.first_run import setup_allowed, setup_host_allowed
        for host in ('127.0.0.1:8501', 'localhost:8501', '[::1]:8501'):
            self.assertTrue(setup_host_allowed(host))
        for host in ('192.168.1.10:8501', '0.0.0.0:8501', 'evil.example:8501', ''):
            self.assertFalse(setup_host_allowed(host))
        self.assertFalse(setup_allowed({'Host': 'example.com:8501'}))
        self.assertFalse(setup_allowed(None))
        self.assertFalse(setup_allowed({'Host': 'localhost:8501'}, server_address='0.0.0.0'))
        self.assertTrue(setup_allowed({'Host': 'localhost:8501'}, server_address='127.0.0.1'))
        with self.store.connection() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], STORAGE_VERSION)
            self.assertGreaterEqual(STORAGE_VERSION, 16)
            auth_columns = {row['name'] for row in c.execute('PRAGMA table_info(auth_users)')}
            worker_columns = {row['name'] for row in c.execute('PRAGMA table_info(worker_lease)')}
            tables = {row['name'] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in ('setup_state', 'recovery_ack', 'setup_package'):
            self.assertIn(name, auth_columns)
        for name in ('pid', 'started_at', 'stop_requested'):
            self.assertIn(name, worker_columns)
        self.assertIn('app_settings', tables)
        token = self.begin()['token']
        initialize(self.store)
        self.assertEqual(self.auth.account_status()['setup_state'], 'recovery_pending')
        self.assertEqual(len(self.auth.pending_recovery(token)), 8)


if __name__ == '__main__':
    unittest.main(verbosity=2)
