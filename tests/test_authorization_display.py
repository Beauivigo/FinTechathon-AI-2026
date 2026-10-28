"""Original authorization evidence stays distinct from payment/UI status."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch
import pyotp

from tests import test_gateway as gateway_fixture
from tests import test_bank_conventions as page_fixture
from bank.auth import AuthService
from bank.gateway import BankGateway
from shared import adapt_request, ServiceError

USER = 'demo_user_a'


class AuthorizationEvidenceTests(unittest.TestCase):
    setUp = gateway_fixture.GatewayTests.setUp
    request = gateway_fixture.GatewayTests.request
    start = gateway_fixture.GatewayTests.start
    payload = gateway_fixture.GatewayTests.payload
    approve = gateway_fixture.GatewayTests.approve
    proof = gateway_fixture.GatewayTests.proof
    restart = gateway_fixture.GatewayTests.restart
    balance = gateway_fixture.GatewayTests.balance
    count = gateway_fixture.GatewayTests.count

    def metadata(self, request):
        return self.gateway.authorization_requirement(request, principal_id=USER)

    def bind_phone(self):
        self.ts = 1800000000
        self.auth = AuthService(self.store, clock=lambda: self.ts, seal=lambda b: b, unseal=lambda b: b)
        self.auth.bootstrap('display-user', 'isolated-test-password')
        self.token = self.auth.login('display-user', 'isolated-test-password')
        self.otp = pyotp.parse_uri(self.auth.enrollment(self.token))
        self.auth.finish_enrollment(self.token, self.otp.at(self.ts))
        self.ts += 30
        self.gateway = BankGateway(self.store, clock=lambda: self.now, auth=self.auth, session_token=self.token)

    def test_green_query_metadata_has_no_business_side_effect(self):
        request = adapt_request('account.balance', {'account_id': 'demo_account_a'}, request_id='display', trace_id='display')
        before = [self.count(t) for t in ('operations', 'challenges', 'transfers', 'audit_events')]
        for _ in range(3):
            self.assertEqual(self.metadata(request), dict(risk_level='green', methods=[]))
        self.assertEqual(before, [self.count(t) for t in ('operations', 'challenges', 'transfers', 'audit_events')])

    def test_exact_threshold_and_completed_transfer_do_not_count_self_twice(self):
        request = self.request(100000)
        self.approve(self.start(request))
        self.restart()
        self.assertEqual(self.metadata(request)['risk_level'], 'yellow')
        self.assertEqual(self.count('transfers'), 1)

    def test_small_transfer_over_daily_total_is_red(self):
        self.approve(self.start(self.request(80000)))
        request = self.request(30000)
        self.start(request)
        self.assertEqual(self.metadata(request), dict(risk_level='red', methods=['demo_otp']))

    def test_cross_account_total_retains_original_red_requirement(self):
        self.approve(self.start(self.request(80000)))
        with self.store.connection(write=True) as c:
            c.execute("INSERT INTO accounts VALUES ('second','demo_user_a','第二账户','CNY',50000,50000,50000,0)")
        request = self.request(30000)
        request['params']['account_id'] = 'second'
        self.start(request)
        self.assertEqual(self.metadata(request)['risk_level'], 'red')

    def test_concurrent_confirmation_upgrade_uses_new_original_challenge(self):
        first, second = self.request(60000), self.request(60000)
        a, b = self.start(first), self.start(second)
        self.assertEqual(self.metadata(second)['risk_level'], 'yellow')
        self.approve(a)
        upgraded = self.approve(b)
        self.assertEqual(upgraded['status'], 'awaiting_verification')
        self.assertEqual(self.metadata(second)['risk_level'], 'red')
        self.assertEqual(self.count('transfers'), 1)

    def test_red_read_does_not_expire_approve_or_downgrade_original(self):
        request = self.request(120000)
        self.start(request)
        before = [self.count(t) for t in ('operations', 'challenges', 'transfers', 'audit_events')]
        self.now += timedelta(days=1)
        self.assertEqual(self.metadata(request), dict(risk_level='red', methods=['demo_otp']))
        self.assertEqual(before, [self.count(t) for t in ('operations', 'challenges', 'transfers', 'audit_events')])
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT state FROM challenges').fetchone()[0], 'pending')

    def test_completed_demo_red_stays_red_on_another_day(self):
        request = self.request(120000)
        result = self.start(request)
        self.approve(result, proof_token=self.proof(result))
        self.now += timedelta(days=1)
        self.assertEqual(self.metadata(request), dict(risk_level='red', methods=['demo_otp']))

    def test_unprepared_or_missing_challenge_is_not_guessed(self):
        request = self.request(90000)
        self.assertIsNone(self.metadata(request)['risk_level'])
        self.approve(self.start(request))
        with self.store.connection(write=True) as c:
            c.execute('DELETE FROM challenges')
        self.assertIsNone(self.metadata(request)['risk_level'])

    def test_changed_parameters_and_foreign_identity_are_rejected(self):
        request = self.request()
        self.start(request)
        changed = deepcopy(request)
        changed['params']['amount_minor'] += 1
        with self.assertRaises(ServiceError):
            self.metadata(changed)
        with self.assertRaises(ServiceError):
            self.gateway.authorization_requirement(request, principal_id='demo_user_li')

    def test_phone_mode_pending_and_history_survive_logout_without_secret_metadata(self):
        self.bind_phone()
        request = self.request(120000)
        request['schema_version'] = '1.2'
        result = self.start(request)
        self.assertEqual(self.metadata(request), dict(risk_level='red', methods=['totp']))
        token = self.gateway.issue_totp_proof(result['next_action']['challenge_id'], self.otp.at(self.ts), principal_id=USER)
        payload = self.payload(result, proof_token=token)
        payload['schema_version'] = '1.2'
        self.assertEqual(self.gateway.authorize(payload, principal_id=USER)['status'], 'succeeded')
        self.auth.logout(self.token)
        with self.assertRaises(ServiceError):
            self.metadata(request)
        self.token = self.auth.login('display-user', 'isolated-test-password')
        self.gateway = BankGateway(self.store, clock=lambda: self.now, auth=self.auth, session_token=self.token)
        self.assertEqual(self.metadata(request), dict(risk_level='red', methods=['totp']))

    def test_old_demo_receipt_is_not_relabeled_as_real_phone_verification(self):
        request = self.request(120000)
        result = self.start(request)
        self.approve(result, proof_token=self.proof(result))
        self.bind_phone()
        self.assertEqual(self.metadata(request), dict(risk_level='red', methods=['demo_otp']))


class AuthorizationPageTests(unittest.TestCase):
    setUp = page_fixture.BankConventionPages.setUp
    page = page_fixture.BankConventionPages.page
    prepare = page_fixture.BankConventionPages.prepare

    @staticmethod
    def badges(page):
        return '\n'.join(m.value for m in page.markdown if '-badge[' in m.value)

    def test_yellow_completed_transfer_shows_separate_original_level(self):
        page = self.page()
        flow = self.prepare(page, amount='900.00')
        self.assertIn('授权级别：黄色', self.badges(page))
        page.button(key='transfer_approve').click().run()
        self.assertFalse(page.exception)
        self.assertEqual(flow.result['status'], 'succeeded')
        self.assertIn('授权级别：黄色', self.badges(page))
        self.assertIn(':green-badge[已完成]', self.badges(page))
        self.assertIn('执行状态', [c.value for c in page.caption])
        self.assertNotIn('授权级别：红色', self.badges(page))

    def test_red_demo_success_keeps_red_permission_beside_green_success(self):
        page = self.page()
        self.prepare(page, amount='1200.00')
        self.assertIn('授权级别：红色 · 强验证', self.badges(page))
        self.assertIn('验证方式：体验模拟验证', [c.value for c in page.caption])
        self.assertNotIn('手机动态码', self.badges(page))
        page.button(key='transfer_approve').click().run()
        self.assertFalse(page.exception)
        self.assertIn('授权级别：红色', self.badges(page))
        self.assertIn(':green-badge[已完成]', self.badges(page))

    def test_pure_green_query_uses_same_separate_labels(self):
        page = self.page('pages/2_产品对比.py')
        page.button(key='product_compare').click().run()
        self.assertFalse(page.exception)
        self.assertIn('授权级别：绿色 · 自动执行', self.badges(page))
        self.assertIn(':green-badge[已完成]', self.badges(page))
