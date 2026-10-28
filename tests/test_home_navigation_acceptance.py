"""Independent real AppTest acceptance for home shortcuts and business gates."""
import unittest
from unittest.mock import patch

from shared import ServiceError
from tests import test_ui_demo_acceptance as ui_acceptance


class HomeNavigationAcceptanceTests(unittest.TestCase):
    setUp = ui_acceptance.UiDemoAcceptanceTests.setUp
    page = ui_acceptance.UiDemoAcceptanceTests.page
    snapshot = ui_acceptance.UiDemoAcceptanceTests.snapshot

    def test_home_shortcuts_change_views_without_creating_a_request(self):
        page = self.page()
        flow = page.session_state['conversation']
        before = self.snapshot(flow)
        self.assertEqual(page.session_state['home_workspace'], '首页')
        self.assertIn('首页', [title.value for title in page.title])
        page.button(key='home_service_transfer').click().run()
        self.assertFalse(page.exception)
        self.assertEqual(page.session_state['home_workspace'], '转账与收款')
        self.assertIsNone(flow.request)
        self.assertEqual(self.snapshot(flow), before)
        page.button(key='home_service_bills').click().run()
        self.assertFalse(page.exception)
        self.assertEqual(page.session_state['home_workspace'], '收支明细')
        self.assertEqual(self.snapshot(flow), before)

    def test_every_other_home_service_navigates_and_returns_without_business_writes(self):
        destinations = {
            'cards': '卡片管理', 'aa': 'AA 收款', 'scheduled': '预约转账',
            'budget': '预算预留与释放', 'wealth': '理财操作', 'products': '产品对比',
        }
        for action, title in destinations.items():
            with self.subTest(service=action):
                page = self.page()
                flow = page.session_state['conversation']
                before = self.snapshot(flow)
                page.button(key='home_service_' + action).click().run()
                self.assertFalse(page.exception)
                self.assertIn(title, [heading.value for heading in page.title])
                self.assertEqual(self.snapshot(flow), before)
                page.button(key='ui_menu_home').click().run()
                self.assertFalse(page.exception)
                self.assertIn('首页', [heading.value for heading in page.title])
                self.assertEqual(self.snapshot(flow), before)

    def test_pending_confirmation_and_unavailable_data_stay_explicit(self):
        page = self.page()
        flow = page.session_state['conversation']
        page.chat_input[0].set_value('给李明转200元').run()
        self.assertFalse(page.exception)
        self.assertEqual(page.session_state['home_workspace'], '转账与收款')
        self.assertEqual(flow.result['status'], 'awaiting_confirmation')
        self.assertEqual(len(self.snapshot(flow)['transfers']), 0)
        self.assertTrue(any('有 1 项业务待处理' in item.value for item in page.warning))
        execute = flow.gateway.execute

        def unavailable(request, **kwargs):
            if request['action'] in {'account.balance', 'transaction.list'}:
                raise ServiceError('UPSTREAM_ERROR', 'Acceptance query failure')
            return execute(request, **kwargs)

        with patch.object(flow.gateway, 'execute', side_effect=unavailable):
            page.run()
        self.assertFalse(page.exception)
        text = '\n'.join(item.value for item in page.markdown)
        self.assertIn('账户信息暂不可用', text)
        self.assertIn('近期交易暂不可用', text)
        self.assertEqual(flow.result['status'], 'awaiting_confirmation')
        self.assertEqual(len(self.snapshot(flow)['transfers']), 0)


if __name__ == '__main__':
    unittest.main()
