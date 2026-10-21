"""实际Streamlit模型页接线：模拟模型输出与真实临时银行，不联网。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from bank.store import Store
from bank import seed

PAGE=Path(__file__).resolve().parents[1]/'app/pages/0_AI银行助手.py'


class ModelPageAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        # Other page tests may first import app.demo while demo_path is patched.
        # Bind this fixture to the real function so the local root patch takes effect.
        rebound=patch('app.demo.demo_path',new=seed.demo_path)
        rebound.start();self.addCleanup(rebound.stop)

    def balance(self):
        with Store(self.root/'runtime/sandbox.sqlite3').connection() as c:
            return c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0]

    def test_model_transfer_uses_original_confirmation_and_rerun_does_not_repeat(self):
        output={'reply':'请核对草稿','action':'propose','intent':'transfer','slots':{'account_id':'demo_account_a','payee_query':'李明','amount_text':'20元'}}
        with patch('bank.seed.PROJECT_ROOT',self.root),patch('agent.model_client.ModelClient.__call__',return_value=json.dumps(output,ensure_ascii=False)) as model:
            app=AppTest.from_file(str(PAGE),default_timeout=15).run()
            self.assertFalse(app.exception);model.assert_not_called()
            app.text_area(key='bank_model_input').set_value('给李明转20元')
            app.button(key='bank_model_send').click().run()
            self.assertFalse(app.exception);self.assertEqual(model.call_count,1)
            self.assertEqual(self.balance(),200000)
            app.button(key='model_prepare_step').click().run()
            self.assertFalse(app.exception);self.assertEqual(self.balance(),200000)
            self.assertTrue(app.text_area(key='bank_model_input').disabled)
            app.button(key='action_approve').click().run()
            self.assertFalse(app.exception);self.assertEqual(self.balance(),198000)
            app.run();app.run()
            self.assertEqual(self.balance(),198000);self.assertEqual(model.call_count,1)

    def test_green_query_runs_without_extra_confirmation(self):
        output={'reply':'准备查询','action':'propose','intent':'balance','slots':{'account_id':'demo_account_a'}}
        with patch('bank.seed.PROJECT_ROOT',self.root),patch('agent.model_client.ModelClient.__call__',return_value=json.dumps(output,ensure_ascii=False)) as model:
            app=AppTest.from_file(str(PAGE),default_timeout=15).run()
            app.text_area(key='bank_model_input').set_value('看看还有多少钱')
            app.button(key='bank_model_send').click().run()
            self.assertFalse(app.exception)
            runner=app.session_state['model_runner']
            self.assertEqual(runner.receipts['t1']['data']['balance_minor'],200000)
            self.assertEqual(runner.tasks[0]['status'],'completed')
            app.run();self.assertEqual(model.call_count,1)

    def test_pause_and_handoff_do_not_call_model_or_write_bank(self):
        with patch('bank.seed.PROJECT_ROOT',self.root),patch('agent.model_client.ModelClient.__call__') as model:
            app=AppTest.from_file(str(PAGE),default_timeout=15).run()
            app.button(key='bank_model_pause').click().run()
            app.text_area(key='bank_model_input').set_value('给李明转钱')
            app.button(key='bank_model_send').click().run()
            self.assertFalse(app.exception);model.assert_not_called()
            app.button(key='bank_model_handoff').click().run()
            self.assertEqual(app.session_state['model_dialogue'].snapshot()['phase'],'handoff')
            self.assertEqual(self.balance(),200000)

    def test_fresh_page_recovers_original_transfer_details_without_new_model_call(self):
        output={'reply':'草稿','action':'propose','intent':'transfer','slots':{'account_id':'demo_account_a','payee_query':'李明','amount_text':'20元'}}
        with patch('bank.seed.PROJECT_ROOT',self.root),patch('agent.model_client.ModelClient.__call__',return_value=json.dumps(output,ensure_ascii=False)) as model:
            first=AppTest.from_file(str(PAGE),default_timeout=15).run()
            first.text_area(key='bank_model_input').set_value('给李明转20元')
            first.button(key='bank_model_send').click().run()
            first.button(key='model_prepare_step').click().run()
            second=AppTest.from_file(str(PAGE),default_timeout=15).run()
            self.assertTrue(second.text_area(key='bank_model_input').disabled)
            second.button(key='action_recover').click().run()
            self.assertFalse(second.exception)
            visible=' '.join(t.value for t in second.text)
            self.assertIn('20.00元',visible);self.assertIn('李明',visible)
            self.assertEqual(model.call_count,1)
            second.button(key='action_reject').click().run()
            self.assertEqual(self.balance(),200000)
