"""Codex页面验收：只读路由不得绕过原确认与恢复。"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


class RoutingPageAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root_patch = patch('bank.seed.PROJECT_ROOT', Path(self.temp.name))
        self.root_patch.start(); self.addCleanup(self.root_patch.stop)
        self.p = AppTest.from_file(str(ROOT/'app/main.py'), default_timeout=15).run()
        self.assertFalse(self.p.exception)

    def snapshot(self):
        flow = self.p.session_state['conversation']
        with flow.gateway.store.connection() as c:
            return {table: [tuple(row) for row in c.execute('SELECT * FROM '+table)]
                    for table in ('accounts','operations','transfers','merchant_orders','reservations','subscription_reminders','card_applications')}

    def identify(self, text):
        self.p.text_input(key='router_input').set_value(text)
        self.p.button(key='router_identify').click().run()
        self.assertFalse(self.p.exception)
        return self.p.session_state['router_result']

    def test_followup_clear_and_rerun_have_no_bank_effect(self):
        before = self.snapshot()
        self.assertEqual(self.identify('帮我准备生日')['status'], 'clarify')
        self.assertEqual(self.identify('2026-11-20')['status'], 'ready')
        self.p.run(); self.assertFalse(self.p.exception)
        self.assertEqual(self.p.session_state['router_result']['fields']['birthday'], '2026-11-20')
        self.p.button(key='router_clear').click().run()
        self.assertFalse(self.p.session_state.get('router_result'))
        self.assertEqual(self.snapshot(), before)

    def test_negative_multi_intent_unsupported_do_not_navigate(self):
        before = self.snapshot()
        for text in ('不要取消订阅', '查账单然后冻结卡片'):
            result = self.identify(text)
            self.assertIsNone(result['intent'])
            buttons = [b for b in self.p.button if b.key == 'router_open']
            self.assertTrue(not buttons or buttons[0].disabled)
        self.assertEqual(self.snapshot(), before)

    def test_navigation_to_subscription_keeps_business_unexecuted(self):
        before = self.snapshot()
        result = self.identify('取消订阅 SUB-E001')
        self.assertEqual(result['intent'], 'subscription')
        self.p.button(key='router_open').click().run()
        self.assertFalse(self.p.exception)
        self.assertEqual(self.p.session_state['routing_handoff']['fields']['subscription_id'], 'SUB-E001')
        self.assertTrue(any('订阅' in title.value for title in self.p.title))
        self.assertEqual(self.snapshot(), before)

    def test_pending_and_unknown_block_router_without_clearing_business(self):
        self.p.chat_input[0].set_value('给李明转200元').run()
        flow = self.p.session_state['conversation']
        self.assertEqual(flow.result['status'], 'awaiting_confirmation')
        original_key = flow.request['idempotency_key']; before = self.snapshot()
        self.assertTrue(self.p.text_input(key='router_input').disabled)
        self.assertTrue(self.p.button(key='router_identify').disabled)
        flow.needs_reconciliation = True
        self.p.run()
        self.assertTrue(self.p.button(key='router_identify').disabled)
        self.assertEqual(flow.request['idempotency_key'], original_key)
        self.assertEqual(self.snapshot(), before)

    def test_other_page_unknown_blocks_route(self):
        from types import SimpleNamespace
        self.p.session_state['action_flow'] = SimpleNamespace(needs_reconciliation=True)
        self.p.run()
        self.assertTrue(self.p.text_input(key='router_input').disabled)
        self.assertTrue(self.p.button(key='router_identify').disabled)

    def test_transfer_route_does_not_create_request(self):
        before = self.snapshot()
        self.assertEqual(self.identify('从账户 demo_account_a 给李明转200元')['status'], 'ready')
        self.p.button(key='router_open').click().run()
        self.assertFalse(self.p.exception)
        self.assertIsNone(self.p.session_state['conversation'].request)
        self.assertEqual(self.snapshot(), before)

    def test_other_destinations_are_real_pages_and_read_only(self):
        for text, title in (
            ('冻结卡片 CARD-E001', '卡片'),
            ('AA收款', 'AA'),
            ('明天给李明转200元', '定时'),
            ('生日2026-11-20', '生日'),
            ('产品对比', '产品'),
            ('查账户 ACCT-C001 的2026-10账单', '资料账单'),
        ):
            with self.subTest(text=text):
                self.p.switch_page('main.py').run()
                before = self.snapshot()
                self.identify(text)
                self.p.button(key='router_open').click().run()
                self.assertFalse(self.p.exception)
                self.assertTrue(any(title in item.value for item in self.p.title))
                self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
