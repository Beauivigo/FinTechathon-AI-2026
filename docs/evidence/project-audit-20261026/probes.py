"""Independent audit probes; temporary identities/ledger, no real HTTP."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import hashlib, json, secrets, sys, unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import pyotp
from bank.auth import AuthService
from bank.store import Store
from bank.seed import initialize
from bank.gateway import BankGateway
from bank.plan_store import PlanStore
from bank.worker import Worker
from agent.controller import Conversation
from agent.model_bank import BankPlanRunner, compile_task
from agent.model_dialogue import DialogueSession
from shared import ServiceError


class AuditProbes(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(dir=ROOT / '.cache' / 'audit-20261026-temp')
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'probe.sqlite3')
        initialize(self.store)
        self.now = 1900000000.0
        self.auth = AuthService(self.store, clock=lambda: self.now)
        self.password = secrets.token_urlsafe(24)
        self.auth.bootstrap('temporary-audit-user', self.password)
        self.token = self.auth.login('temporary-audit-user', self.password)
        self.otp = pyotp.parse_uri(self.auth.enrollment(self.token))
        self.auth.finish_enrollment(self.token, self.otp.at(self.now))
        self.now += 30
        self.journal = PlanStore(self.store)

    def flow(self):
        return Conversation(BankGateway(self.store, auth=self.auth, session_token=self.token,
            clock=lambda: datetime.fromtimestamp(self.now, timezone.utc)), principal_id='demo_user_a')

    def ledger(self):
        with self.store.connection() as c:
            return (c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0],
                    c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0])

    def runner(self):
        return BankPlanRunner(self.flow(), [dict(id='payment', title='1200元转账', intent='transfer',
            fields=dict(account_id='demo_account_a', payee_query='李明', amount_text='1200元'), depends_on=[])],
            trace_id='plan_' + secrets.token_hex(16), journal=self.journal)

    def test_real_auth_lost_receipt_relogin_restore_three_times_only_one_payment(self):
        runner = self.runner()
        self.assertEqual(runner.advance(allow_write=True)['status'], 'awaiting_verification')
        original_key = runner.flow.request['idempotency_key']
        original = runner.flow.gateway.authorize
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise ServiceError('RESULT_UNKNOWN', 'temporary lost receipt')
        with patch.object(runner.flow.gateway, 'authorize', side_effect=lost):
            self.assertEqual(runner.flow.decide(True, totp_code=self.otp.at(self.now))['status'], 'unknown')
        self.assertEqual(self.ledger(), (1, 80000))
        self.auth.logout(self.token)
        self.token = self.auth.login('temporary-audit-user', self.password)
        for _ in range(3):
            restored = BankPlanRunner.restore(self.flow(), self.journal, runner.trace_id)
            self.assertEqual(restored.tasks[0]['operation_key'], original_key)
            self.assertEqual(restored.tasks[0]['status'], 'completed')
            self.assertIsNone(restored.advance(allow_write=True))
        self.assertEqual(self.ledger(), (1, 80000))
        with self.store.connection() as c:
            self.assertGreater(c.execute('SELECT COUNT(*) FROM plan_events').fetchone()[0], 1)

    def test_worker_prepares_real_auth_plan_without_login_or_payment_authority(self):
        runner = self.runner(); runner.automation = True; runner.checkpoint()
        self.auth.logout(self.token)
        worker = Worker(self.store, clock=lambda: datetime.fromtimestamp(self.now, timezone.utc))
        self.assertTrue(worker.tick())
        self.assertEqual(self.ledger(), (0, 200000))
        snapshot, _ = self.journal.load(runner.trace_id, 'demo_user_a')
        self.assertEqual(snapshot['tasks'][0]['status'], 'awaiting_bank')
        self.token = self.auth.login('temporary-audit-user', self.password)
        restored = BankPlanRunner.restore(self.flow(), self.journal, runner.trace_id)
        self.assertEqual(restored.flow.result['next_action']['methods'], ['totp'])
        with self.assertRaises(ServiceError): restored.flow.decide(True, totp_code='not-a-code')
        self.assertEqual(self.ledger(), (0, 200000))
        self.assertEqual(restored.flow.decide(True, totp_code=self.otp.at(self.now))['status'], 'succeeded')
        restored._sync(); worker.tick(); worker.close()
        self.assertEqual(self.ledger(), (1, 80000))

    def test_capability_boundary_birthday_intent_only_returns_date_plan(self):
        flow = self.flow()
        action, params = compile_task(flow, 'birthday', {'birthday':'2030-06-20'})
        self.assertEqual(action, 'scenario.plan')
        self.assertEqual(flow.start_action(action, params)['status'], 'succeeded')
        self.assertEqual(self.journal.list('demo_user_a'), [])
        self.assertEqual(self.ledger(), (0, 200000))

    def test_capability_boundary_memo_and_timed_birthday_fields_are_not_in_model_protocol(self):
        cases = [dict(action='propose', intent='transfer', slots=dict(account_id='demo_account_a',
                    payee_query='李明', amount_text='20元', memo='午饭')),
                 dict(action='plan', tasks=[dict(id='budget', title='当月预留', intent='budget_reserve',
                    slots=dict(account_id='demo_account_a', amount_text='1000元', purpose='生日'),
                    depends_on=[], not_before='2030-06-01T09:00:00+08:00')])]
        for response in cases:
            session = DialogueSession(lambda messages: json.dumps(response, ensure_ascii=False))
            self.assertIsNone(session.submit('安排这项业务')['proposal'])
        self.assertEqual(self.ledger(), (0, 200000))


if __name__ == '__main__':
    out = Path(__file__).parent
    paths = [ROOT / 'runtime/sandbox.sqlite3', ROOT / '.cache/bank-model/profile.json']
    def hashes():
        return [hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in paths]
    before = hashes()
    with (out / 'probes.txt').open('w', encoding='utf-8') as log, redirect_stdout(log), redirect_stderr(log), \
            patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('offline only')):
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AuditProbes))
    record = dict(tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  skipped=len(result.skipped), passed=result.wasSuccessful(), daily_data_and_profile_unchanged=before==hashes(),
                  real_http_blocked=True, scope='Two recovery/authorization probes and two verified capability boundaries. Temporary DPAPI/TOTP identities only; no physical phone or model call.')
    (out / 'probes.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False))
    sys.exit(0 if record['passed'] and record['daily_data_and_profile_unchanged'] else 1)
