"""Offline identity and TOTP tests; secrets below are isolated test fixtures."""
import unittest
import pyotp
from bank.auth import AuthService
from bank.gateway import BankGateway
from agent.controller import Conversation
from tests import test_model_bank as fixture
from shared import ServiceError

class AuthTests(unittest.TestCase):
    setUp=fixture.ModelBankTests.setUp
    def setup_auth(self):
        self.ts=1800000000
        self.auth=AuthService(self.store,clock=lambda:self.ts,seal=lambda b:b,unseal=lambda b:b)
        self.codes=self.auth.bootstrap('tester','test-password-12345')
        self.token=self.auth.login('tester','test-password-12345')
        uri=self.auth.enrollment(self.token);self.otp=pyotp.parse_uri(uri)
        self.auth.finish_enrollment(self.token,self.otp.at(self.ts));self.ts+=30
        self.gateway=BankGateway(self.store,clock=self.flow.gateway.clock,auth=self.auth,session_token=self.token)
    def test_password_and_bound_session_and_logout(self):
        self.setup_auth()
        self.assertEqual(self.auth.principal(self.token),'demo_user_a')
        with self.assertRaises(ServiceError):self.auth.login('tester','incorrect-password')
        self.auth.logout(self.token)
        with self.assertRaises(ServiceError):self.gateway.get_context(principal_id='demo_user_a')
    def test_true_red_gate_and_single_use_code(self):
        self.setup_auth();flow=Conversation(self.gateway,principal_id='demo_user_a')
        flow.start_action('transfer.create',{'account_id':'demo_account_a','payee_id':'demo_payee_li','amount_minor':120000})
        self.assertEqual(flow.result['next_action']['methods'],['totp'])
        with self.assertRaises(ServiceError):self.gateway.issue_demo_proof(flow.result['next_action']['challenge_id'],principal_id='demo_user_a')
        self.assertEqual(flow.decide(True,totp_code=self.otp.at(self.ts))['status'],'succeeded')
        with self.store.connection(write=True) as c:self.assertFalse(self.auth.consume(c,self.token,self.otp.at(self.ts)))
    def test_wrong_expired_unicode_and_replay_codes(self):
        self.setup_auth()
        for code in [self.otp.at(self.ts-300),self.otp.at(self.ts+300),'abcdef','１２３４５６']:
            with self.store.connection(write=True) as c:self.assertFalse(self.auth.consume(c,self.token,code))
        with self.store.connection(write=True) as c:
            self.assertTrue(self.auth.consume(c,self.token,self.otp.at(self.ts)))
            self.assertFalse(self.auth.consume(c,self.token,self.otp.at(self.ts)))
    def test_recovery_revokes_sessions_and_is_single_use(self):
        self.setup_auth();self.auth.recover('tester','test-password-12345',self.codes[0])
        with self.assertRaises(ServiceError):self.auth.principal(self.token)
        with self.assertRaises(ServiceError):self.auth.recover('tester','test-password-12345',self.codes[0])
    def test_unbound_or_foreign_session_cannot_use_gateway(self):
        self.setup_auth()
        for token in [None,'fake']:
            with self.assertRaises(ServiceError):BankGateway(self.store,auth=self.auth,session_token=token).get_context(principal_id='demo_user_a')
        with self.assertRaises(ServiceError):self.gateway.get_context(principal_id='demo_user_li')
    def test_rfc_6238_vector(self):
        import base64,hashlib
        secret=base64.b32encode(b'12345678901234567890').decode()
        self.assertEqual(pyotp.TOTP(secret,digits=8,digest=hashlib.sha1).at(59),'94287082')

    def test_totp_lock_cannot_be_reset_by_password_login(self):
        self.setup_auth()
        for _ in range(5):
            with self.store.connection(write=True) as c:self.assertFalse(self.auth.consume(c,self.token,'abcdef'))
        other=self.auth.login('tester','test-password-12345')
        with self.store.connection(write=True) as c:self.assertFalse(self.auth.consume(c,other,self.otp.at(self.ts)))

    def test_concurrent_totp_is_consumed_once(self):
        from concurrent.futures import ThreadPoolExecutor
        self.setup_auth()
        def consume(_):
            with self.store.connection(write=True) as c:return self.auth.consume(c,self.token,self.otp.at(self.ts))
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sorted(pool.map(consume,range(2))),[False,True])

    def test_windows_dpapi_round_trip(self):
        import os
        if os.name!='nt':return  # Platform-specific cryptography is verified on Windows separately.
        from agent.model_credentials import _dpapi
        raw=b'offline-fake-binding-secret'
        encrypted=_dpapi(raw,decrypt=False)
        self.assertNotEqual(encrypted,raw)
        self.assertEqual(_dpapi(encrypted,decrypt=True),raw)

    def test_expired_totp_challenge_is_terminal_without_payment(self):
        from datetime import timedelta
        self.setup_auth();flow=Conversation(self.gateway,principal_id='demo_user_a')
        flow.start_action('transfer.create',{'account_id':'demo_account_a','payee_id':'demo_payee_li','amount_minor':120000})
        now=self.gateway.clock();self.gateway.clock=lambda:now+timedelta(minutes=10)
        with self.assertRaises(ServiceError) as error:self.gateway.issue_totp_proof(flow.result['next_action']['challenge_id'],self.otp.at(self.ts),principal_id='demo_user_a')
        self.assertEqual(error.exception.code,'AUTH_EXPIRED')
        flow.recover();self.assertEqual(flow.result['status'],'failed')
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0],0)

    def test_proof_is_bound_to_issuing_login_session(self):
        from shared import SCHEMA_VERSION
        self.setup_auth();flow=Conversation(self.gateway,principal_id='demo_user_a')
        flow.start_action('transfer.create',{'account_id':'demo_account_a','payee_id':'demo_payee_li','amount_minor':120000})
        ch=flow.result['next_action'];proof=self.gateway.issue_totp_proof(ch['challenge_id'],self.otp.at(self.ts),principal_id='demo_user_a')
        second=self.auth.login('tester','test-password-12345')
        gateway=BankGateway(self.store,auth=self.auth,session_token=second,clock=self.gateway.clock)
        with self.assertRaises(ServiceError):gateway.authorize(dict(schema_version='1.2',challenge_id=ch['challenge_id'],operation_digest=ch['operation_digest'],decision='approve',proof_token=proof),principal_id='demo_user_a')
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0],0)
