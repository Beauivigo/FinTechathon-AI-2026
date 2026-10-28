"""安装 requirements-app.txt 后执行的真实 Streamlit 页面验收。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

try:
    from tests.ui_support import AppTest
except ImportError:
    AppTest = None


@unittest.skipIf(AppTest is None, "页面验收需先安装 requirements-app.txt")
class PageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_patch = patch('bank.seed.PROJECT_ROOT', Path(self.temp.name))
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        path = Path(__file__).resolve().parents[1] / "app/main.py"
        self.page_path = path
        self.app = AppTest.from_file(str(path), default_timeout=10).run()

    def receipts(self):
        chat = self.app.session_state['conversation']
        with chat.gateway.store.connection() as c:
            return c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0]

    def balance(self):
        chat = self.app.session_state['conversation']
        with chat.gateway.store.connection() as c:
            return c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0]

    def button(self, label):
        return next(button for button in self.app.button if button.label == label)

    def test_page_starts_and_missing_amount_then_payee_selection_works(self):
        self.assertEqual(len(self.app.exception), 0)
        self.assertIn("转账与收款", [title.value for title in self.app.title])
        self.app.chat_input[0].set_value("给小王转钱").run()
        chat = self.app.session_state["conversation"]
        self.assertIn("多少元", chat.messages[-1]["content"])
        self.assertIsNone(chat.request)
        self.app.chat_input[0].set_value("200元").run()
        label = "小王 · 模拟银行乙 · ****5678"
        self.button(label).click().run()
        chat = self.app.session_state["conversation"]
        self.assertEqual(chat.request["params"]["payee_id"], "demo_payee_wang_2")
        self.assertEqual(chat.result["status"], "awaiting_confirmation")
        self.assertEqual(self.receipts(), 0)
        self.assertEqual(len(self.app.exception), 0)

    def test_confirmation_and_rerun_only_create_one_persisted_transfer(self):
        self.app.chat_input[0].set_value("给李明转200元").run()
        self.assertTrue(self.app.chat_input[0].disabled)
        self.app.button(key='transfer_approve').click().run()
        self.app.run()
        chat = self.app.session_state["conversation"]
        self.assertEqual(chat.result["status"], "succeeded")
        self.assertEqual(self.receipts(), 1)
        self.assertEqual(self.balance(), 180000)
        self.assertFalse(self.app.chat_input[0].disabled)
        self.assertIn("【模拟】", self.app.success[0].value)
        self.assertEqual(len(self.app.exception), 0)

    def test_rejection_and_simulated_verification_are_separate_paths(self):
        self.app.chat_input[0].set_value("给李明转200元").run()
        self.app.button(key='transfer_cancel').click().run()
        self.assertEqual(self.app.session_state["conversation"].result["status"], "cancelled")
        self.app.chat_input[0].set_value("给李明转1200元").run()
        self.assertEqual(self.app.session_state["conversation"].result["status"], "awaiting_verification")
        self.app.button(key='transfer_approve').click().run()
        chat = self.app.session_state["conversation"]
        self.assertEqual(chat.result["status"], "succeeded")
        self.assertEqual(self.receipts(), 1)
        self.assertEqual(len(self.app.exception), 0)

    def test_optional_bank_name_and_invalid_candidate_input_do_not_break_page(self):
        chat = self.app.session_state["conversation"]
        with chat.gateway.store.connection(write=True) as c:
            c.execute('UPDATE payees SET bank_name=NULL')
        self.app.chat_input[0].set_value("给小王转200元").run()
        self.assertEqual(len(self.app.exception), 0)
        self.button("小王 · 银行未提供 · ****1234")
        self.app.chat_input[0].set_value("给小王转-500元").run()
        self.assertFalse(any("****1234" in button.label for button in self.app.button))
        self.app.chat_input[0].set_value("给小王转200元").run()
        self.button("小王 · 银行未提供 · ****5678").click().run()
        self.assertEqual(self.app.session_state["conversation"].result["status"], "awaiting_confirmation")
        self.assertEqual(len(self.app.exception), 0)

    def test_unknown_write_result_disables_new_submission(self):
        self.app.chat_input[0].set_value("给李明转200元").run()
        chat = self.app.session_state["conversation"]
        authorize = chat.gateway.authorize

        def lost_reply(payload, *, principal_id):
            authorize(payload, principal_id=principal_id)
            raise TimeoutError("reply lost")

        chat.gateway.authorize = lost_reply
        self.app.button(key='transfer_approve').click().run()
        chat = self.app.session_state["conversation"]
        self.assertEqual(chat.result["status"], "unknown")
        self.assertEqual(self.receipts(), 1)
        self.assertTrue(self.app.chat_input[0].disabled)
        self.assertEqual(len(self.app.success), 0)
        self.assertEqual(len(self.app.exception), 0)

    def test_unknown_is_queryable_and_reset_cannot_bypass_it(self):
        self.app.chat_input[0].set_value('给李明转200元').run()
        chat = self.app.session_state['conversation']
        authorize = chat.gateway.authorize
        def lost(payload, **kw):
            authorize(payload, **kw)
            raise TimeoutError('lost')
        chat.gateway.authorize = lost
        self.app.button(key='transfer_approve').click().run()
        self.assertTrue(self.button('新建对话').disabled)
        self.assertTrue(self.button('确认重置模拟银行').disabled)
        self.button('查询原操作结果').click().run()
        self.assertEqual(chat.result['status'], 'succeeded')
        self.assertEqual(self.receipts(), 1)
        self.assertFalse(self.app.chat_input[0].disabled)
        self.assertEqual(len(self.app.exception), 0)

    def test_fresh_page_recovers_pending_and_success_without_automatic_authorization(self):
        self.app.chat_input[0].set_value('给李明转200元').run()
        original = self.app.session_state['conversation'].request
        self.app = AppTest.from_file(str(self.page_path), default_timeout=10).run()
        self.assertTrue(self.app.chat_input[0].disabled)
        self.assertEqual(self.receipts(), 0)
        self.button('恢复选中操作').click().run()
        self.assertEqual(self.app.session_state['conversation'].request, original)
        self.assertEqual(self.app.session_state['conversation'].payee['display_name'], '李明')
        self.app.button(key='transfer_approve').click().run()
        self.app = AppTest.from_file(str(self.page_path), default_timeout=10).run()
        self.button('恢复选中操作').click().run()
        self.assertEqual(self.app.session_state['conversation'].result['status'], 'succeeded')
        self.assertEqual(self.balance(), 180000)
        self.assertEqual(self.receipts(), 1)
        self.assertEqual(len(self.app.exception), 0)

    def test_new_chat_preserves_bank_and_explicit_reset_restores_seed(self):
        self.app.chat_input[0].set_value('给李明转200元').run()
        self.app.button(key='transfer_approve').click().run()
        self.button('新建对话').click().run()
        self.assertEqual(self.balance(), 180000)
        self.assertEqual(self.receipts(), 1)
        self.assertIsNone(self.app.session_state['conversation'].request)
        self.assertTrue(self.button('确认重置模拟银行').disabled)
        self.app.checkbox[0].check().run()
        self.button('确认重置模拟银行').click().run()
        self.assertEqual(self.balance(), 200000)
        self.assertEqual(self.receipts(), 0)
        self.assertEqual(len(self.app.exception), 0)

    def test_cumulative_threshold_page_uses_ledger(self):
        self.app.chat_input[0].set_value('给李明转800元').run()
        self.app.button(key='transfer_approve').click().run()
        self.app.chat_input[0].set_value('给李明转200.01元').run()
        self.assertEqual(self.app.session_state['conversation'].result['status'], 'awaiting_verification')
        self.assertTrue(self.app.button(key='transfer_approve').label.startswith('体验验证并'))
        self.app.button(key='transfer_approve').click().run()
        self.assertEqual(self.balance(), 99999)
        self.assertEqual(self.receipts(), 2)
        self.assertEqual(len(self.app.exception), 0)

    def test_insufficient_funds_page_never_reports_success(self):
        self.app.chat_input[0].set_value('给李明转2001元').run()
        self.app.button(key='transfer_approve').click().run()
        result = self.app.session_state['conversation'].result
        self.assertEqual(result['error']['code'], 'INSUFFICIENT_FUNDS')
        self.assertEqual(len(self.app.success), 0)
        self.assertEqual(self.balance(), 200000)
        self.assertEqual(self.receipts(), 0)
        self.assertEqual(len(self.app.exception), 0)


if __name__ == "__main__":
    unittest.main()
