"""Independent continuity checks; all bank state is synthetic and temporary."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import app
from bank.plan_store import PlanStore
from tests.ui_support import AppTest

ROOT = Path(app.__file__).resolve().parents[1]


def proposal(intent, fields):
    return json.dumps(dict(action='propose', reply='请核对未执行的草稿。', intent=intent,
                           slots=dict(account_id='demo_account_a', **fields)), ensure_ascii=False)


class FinalContinuityTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='fintech-final-independent-')
        self.addCleanup(temp.cleanup)
        root_patch = patch('bank.seed.PROJECT_ROOT', Path(temp.name))
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def page(self):
        page = AppTest.from_file(str(ROOT/'app/pages/0_AI银行助手.py'), default_timeout=20).run()
        self.assertFalse(page.exception)
        return page

    def send(self, page, text):
        page.text_area(key='bank_model_input').set_value(text)
        page.button(key='bank_model_send').click().run()
        self.assertFalse(page.exception)

    def test_observation_does_not_replace_an_unknown_committed_transfer(self):
        with patch('agent.model_client.ModelClient.__call__', return_value=proposal('balance', {})):
            page = self.page()
            flow = page.session_state['model_bank_flow']
            flow.start_action('transfer.create', dict(account_id='demo_account_a',
                payee_id='demo_payee_li', amount_minor=2000))
            authorize = flow.gateway.authorize

            def lose_reply(*args, **kwargs):
                authorize(*args, **kwargs)
                raise TimeoutError('synthetic committed response lost')

            with patch.object(flow.gateway, 'authorize', side_effect=lose_reply):
                flow.decide(True)
            original = deepcopy(flow.request)
            unknown = deepcopy(flow.result)
            self.assertEqual(unknown['status'], 'unknown')
            page.run()
            self.send(page, '先查一下现在余额，原转账继续保留待核实')
            runner = page.session_state['model_runner']
            self.assertIn('t1', runner.receipts)
            self.assertEqual(runner.receipts['t1']['data']['balance_minor'], 198000)
            self.assertEqual(flow.request, original)
            self.assertEqual(flow.result, unknown)
            self.assertTrue(flow.needs_reconciliation)
            with flow.gateway.store.connection() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0], 1)

    def test_side_query_preserves_the_pending_original_plan(self):
        replies = [proposal('budget_reserve', {'amount_text':'100元','purpose':'保留原计划'}),
                   proposal('balance', {})]
        with patch('agent.model_client.ModelClient.__call__', side_effect=replies):
            page = self.page()
            self.send(page, '预留100元，供之后使用')
            page.button(key='model_prepare_step').click().run()
            self.assertFalse(page.exception)
            flow = page.session_state['model_bank_flow']
            original_request = deepcopy(flow.request)
            old_runner = page.session_state['model_runner']
            original_id = old_runner.trace_id
            journal = PlanStore(flow.gateway.store)
            original_snapshot = journal.load(original_id, flow.principal_id)[0]
            self.assertTrue(flow.pending)
            self.send(page, '另外看看余额，不修改刚才的预留')
            runner = page.session_state['model_runner']
            self.assertIn('t1', runner.receipts)
            self.assertEqual(runner.receipts['t1']['data']['balance_minor'], 200000)
            restored = journal.load(original_id, flow.principal_id)[0]
            self.assertEqual(restored['tasks'], original_snapshot['tasks'])
            self.assertEqual(restored['waiting'], original_snapshot['waiting'])
            self.assertEqual(flow.request, original_request)
            self.assertTrue(flow.pending)
            with flow.gateway.store.connection() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM reservations').fetchone()[0], 0)

    def test_controls_change_on_the_same_click_without_an_extra_rerun(self):
        with patch('agent.model_client.ModelClient.__call__',
                   return_value=proposal('transfer', {'payee_query':'李明','amount_text':'20元'})) as model:
            page = self.page()
            self.send(page, '给李明转20元')
            page.button(key='bank_model_pause').click().run()
            self.assertTrue(page.button(key='bank_model_send').disabled)
            self.assertTrue(any(b.key=='bank_model_resume' for b in page.button))
            page.button(key='bank_model_resume').click().run()
            self.assertFalse(page.button(key='bank_model_send').disabled)
            page.button(key='bank_model_handoff').click().run()
            self.assertTrue(page.button(key='bank_model_send').disabled)
            self.assertTrue(any(b.key=='bank_model_manual_transfer' for b in page.button))
            self.assertEqual(model.call_count, 1)

    def test_model_gets_the_card_names_used_by_the_manual_page_without_mutating_context(self):
        from agent.model_runtime import TrustedModelCaller
        from agent.controller import Conversation
        from bank.gateway import BankGateway
        from bank.seed import initialize
        from bank.store import Store
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td)/'bank.sqlite3')
            initialize(store)
            flow = Conversation(BankGateway(store), principal_id='demo_user_a')
            original = deepcopy(flow.gateway.get_context(principal_id=flow.principal_id))
            class Client:
                last_metadata = {}
                def __call__(self, messages):
                    self.messages = messages
                    return '{}'
            client = Client()
            TrustedModelCaller(flow, client_factory=lambda:client)([dict(role='system',content='rules')])
            self.assertIn('白金卡', client.messages[0]['content'])
            self.assertIn('CARD-E001', client.messages[0]['content'])
            self.assertEqual(flow.gateway.get_context(principal_id=flow.principal_id), original)

    def test_unrelated_provider_environment_does_not_claim_the_selected_model_is_configured(self):
        from agent.model_credentials import CredentialStore
        from agent.model_providers import PROVIDERS
        from streamlit.testing.v1 import AppTest as NativeAppTest
        import tempfile
        env = dict.fromkeys(['BANK_MODEL_PROVIDER','BANK_MODEL_BASE_URL','BANK_MODEL_API_KEY',
                             'BANK_MODEL_USE_DESKTOP_PET'] +
                            [n for p in PROVIDERS.values() for n in p.key_envs], '')
        import secrets
        env.update(BANK_MODEL_PROVIDER='deepseek', ZHIPU_API_KEY=secrets.token_urlsafe(18))
        with tempfile.TemporaryDirectory() as td:
            store = CredentialStore(Path(td)/'profile.json')
            with patch('app.model_settings.CredentialStore', return_value=store), \
                 patch.object(store,'key',side_effect=AssertionError('status must not decrypt keys')), \
                 patch.dict('os.environ',env):
                page = NativeAppTest.from_string('from app.model_settings import render_configuration_status\nrender_configuration_status()').run(timeout=20)
                self.assertFalse(page.exception)
                self.assertTrue(any('尚未配置模型' in e.value for e in page.warning))

    def test_balance_receipt_shows_authoritative_amount_and_unknown_schedule_stays_unresolved(self):
        from agent.controller import Conversation
        from app.presentation import receipt_details, schedule_display
        from bank.gateway import BankGateway
        from bank.seed import initialize
        from bank.store import Store
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td)/'bank.sqlite3'); initialize(store)
            flow = Conversation(BankGateway(store), principal_id='demo_user_a')
            flow.start_action('account.balance',dict(account_id='demo_account_a'))
            lines = receipt_details(flow.request, flow.result)
            self.assertTrue(any('2,000.00' in s or '2000.00' in s for s in lines))
            for status in ('unknown',None,'NOT_FOUND'):
                self.assertIn('待核实', schedule_display('dispatched',status)[0])

    def test_another_page_hides_completed_receipt_but_keeps_pending_recovery(self):
        page = AppTest.from_file(str(ROOT/'app/main.py'),default_timeout=20).run()
        page.switch_page('pages/3_卡片管理.py').run()
        self.assertFalse(page.exception)
        page.button(key='card_submit').click().run()
        flow = page.session_state['action_flow']
        original = deepcopy(flow.request)
        page.switch_page('pages/4_AA收款.py').run()
        self.assertTrue(any('当前操作：冻结' in h.value for h in page.subheader))
        flow.decide(True,simulate_verification=True)
        page.run()
        self.assertFalse(page.exception)
        self.assertFalse(any('当前操作：冻结' in h.value for h in page.subheader))
        self.assertEqual(flow.request, original)
        self.assertEqual(flow.result['status'],'succeeded')


if __name__ == '__main__':
    unittest.main()
