"""安装 requirements-app.txt 后执行的真实 Streamlit 页面验收。"""

from pathlib import Path
import unittest

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


@unittest.skipIf(AppTest is None, "页面验收需先安装 requirements-app.txt")
class PageTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "app/main.py"
        self.app = AppTest.from_file(str(path), default_timeout=10).run()

    def button(self, label):
        return next(button for button in self.app.button if button.label == label)

    def test_page_starts_and_missing_amount_then_payee_selection_works(self):
        self.assertEqual(len(self.app.exception), 0)
        self.assertIn("把一笔转账说清楚", [title.value for title in self.app.title])
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
        self.assertEqual(chat.gateway.receipt_count, 0)
        self.assertEqual(len(self.app.exception), 0)

    def test_confirmation_and_rerun_only_create_one_sample_receipt(self):
        self.app.chat_input[0].set_value("给李明转200元").run()
        self.assertTrue(self.app.chat_input[0].disabled)
        self.button("确认此笔示例转账").click().run()
        self.app.run()
        chat = self.app.session_state["conversation"]
        self.assertEqual(chat.result["status"], "succeeded")
        self.assertEqual(chat.gateway.receipt_count, 1)
        self.assertFalse(self.app.chat_input[0].disabled)
        self.assertIn("【模拟】", self.app.success[0].value)
        self.assertEqual(len(self.app.exception), 0)

    def test_rejection_and_simulated_verification_are_separate_paths(self):
        self.app.chat_input[0].set_value("给李明转200元").run()
        self.button("拒绝此操作").click().run()
        self.assertEqual(self.app.session_state["conversation"].result["status"], "cancelled")
        self.app.chat_input[0].set_value("给李明转1200元").run()
        self.assertEqual(self.app.session_state["conversation"].result["status"], "awaiting_verification")
        self.button("模拟验证并继续").click().run()
        chat = self.app.session_state["conversation"]
        self.assertEqual(chat.result["status"], "succeeded")
        self.assertEqual(chat.gateway.receipt_count, 1)
        self.assertEqual(len(self.app.exception), 0)

    def test_optional_bank_name_and_invalid_candidate_input_do_not_break_page(self):
        chat = self.app.session_state["conversation"]
        chat.gateway.payees = [{k: v for k, v in row.items() if k != "bank_name"} for row in chat.gateway.payees]
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
        self.button("确认此笔示例转账").click().run()
        chat = self.app.session_state["conversation"]
        self.assertEqual(chat.result["status"], "unknown")
        self.assertEqual(chat.gateway.receipt_count, 1)
        self.assertTrue(self.app.chat_input[0].disabled)
        self.assertEqual(len(self.app.success), 0)
        self.assertEqual(len(self.app.exception), 0)


if __name__ == "__main__":
    unittest.main()
