"""Codex independent UI acceptance against real Streamlit pages and temporary data."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from bank import seed


ROOT = Path(__file__).resolve().parents[1]


class UiDemoAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.data_root = Path(temporary.name)
        self.addCleanup(temporary.cleanup)
        self.seed_patch = patch('bank.seed.PROJECT_ROOT', self.data_root)
        self.seed_patch.start()
        self.addCleanup(self.seed_patch.stop)
        # Older tests can first import app.demo while bank.seed.demo_path is mocked.
        # Rebind the captured name so this test always uses its own real temporary DB.
        demo_path_patch = patch('app.demo.demo_path', new=seed.demo_path)
        demo_path_patch.start()
        self.addCleanup(demo_path_patch.stop)

    def page(self, relative='main.py'):
        page = AppTest.from_file(str(ROOT / 'app' / relative), default_timeout=15).run()
        self.assertFalse(page.exception)
        return page

    def snapshot(self, flow):
        with flow.gateway.store.connection() as connection:
            return {table: [tuple(row) for row in connection.execute('SELECT * FROM ' + table)]
                    for table in ('accounts', 'transfers', 'operations', 'holdings', 'wealth_orders')}

    def badge_contains(self, page, text):
        # Streamlit's public badge command renders a colored Markdown badge.
        return any('badge[' + text + ']' in element.value for element in page.markdown)

    def test_every_page_loads_independently_without_business_writes(self):
        home = self.page()
        flow = home.session_state['conversation']
        before = self.snapshot(flow)
        for source in sorted((ROOT / 'app' / 'pages').glob('*.py')):
            with self.subTest(page=source.name):
                page = self.page('pages/' + source.name)
                self.assertTrue(page.title)
                self.assertEqual(self.snapshot(flow), before)

    def test_primary_navigation_round_trip_keeps_the_ledger(self):
        page = self.page()
        flow = page.session_state['conversation']
        before = self.snapshot(flow)
        page.button(key='home_service_wealth').click().run()
        self.assertFalse(page.exception)
        self.assertIn('理财操作', [heading.value for heading in page.title])
        self.assertEqual(self.snapshot(flow), before)
        page.button(key='app_nav_home').click().run()
        self.assertFalse(page.exception)
        self.assertIn('个人银行首页', [heading.value for heading in page.title])
        self.assertEqual(self.snapshot(flow), before)

    def test_transfer_status_requires_confirmation_and_rerun_never_repeats_payment(self):
        page = self.page()
        flow = page.session_state['conversation']
        page.chat_input[0].set_value('给李明转200元').run()
        self.assertFalse(page.exception)
        self.assertEqual(flow.result['status'], 'awaiting_confirmation')
        self.assertTrue(self.badge_contains(page, '待确认'))
        self.assertEqual(len(self.snapshot(flow)['transfers']), 0)
        next(button for button in page.button if button.label == '确认此笔模拟转账').click().run()
        self.assertFalse(page.exception)
        self.assertEqual(flow.result['status'], 'succeeded')
        self.assertTrue(self.badge_contains(page, '已完成'))
        after = self.snapshot(flow)
        page.run()
        self.assertEqual(self.snapshot(flow), after)
        self.assertEqual(len(after['transfers']), 1)

    def test_wealth_ui_preserves_the_strong_verification_gate(self):
        page = self.page('pages/9_理财.py')
        initial = page.session_state['action_flow']
        self.assertEqual(initial.gateway.store.path, self.data_root / 'runtime' / 'sandbox.sqlite3')
        self.assertFalse(page.button(key='wealth_assess').disabled,
                         {'path': str(initial.gateway.store.path),
                          'request': initial.request, 'result': initial.result,
                          'external_blocked': initial.external_blocked,
                          'needs_reconciliation': initial.needs_reconciliation})
        page.button(key='wealth_assess').click().run()
        self.assertFalse(page.exception)
        flow = page.session_state['action_flow']
        self.assertEqual(flow.result['status'], 'succeeded')
        before = self.snapshot(flow)
        page.button(key='wealth_purchase').click().run()
        self.assertFalse(page.exception)
        self.assertEqual(flow.result['status'], 'awaiting_verification')
        self.assertTrue(self.badge_contains(page, '待模拟验证'))
        after = self.snapshot(flow)
        self.assertEqual(after['accounts'], before['accounts'])
        self.assertEqual(after['holdings'], before['holdings'])
        page.button(key='action_approve').click().run()
        self.assertFalse(page.exception)
        self.assertEqual(flow.result['status'], 'succeeded')
        self.assertTrue(self.badge_contains(page, '已完成'))
        self.assertEqual(len(self.snapshot(flow)['holdings']), 1)


if __name__ == '__main__':
    unittest.main()
