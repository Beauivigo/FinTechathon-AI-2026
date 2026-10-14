"""B3 真实 Streamlit 页面测试，所有历史均位于临时模拟库。"""
from copy import deepcopy
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from shared import ServiceError
from tests.test_app import AppTest
from tests import test_billing_gateway as billing_helpers


@unittest.skipIf(AppTest is None, '页面验收需先安装 requirements-app.txt')
class BillingPageTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = patch('bank.seed.PROJECT_ROOT', Path(temp.name))
        root.start()
        self.addCleanup(root.stop)
        self.app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app/main.py'), default_timeout=10).run()
        self.chat = self.app.session_state['conversation']
        with self.chat.gateway.store.connection(write=True) as c:
            for i, (amount, kind, direction, category) in enumerate([
                (10000, 'consumption', 'debit', '餐饮'), (2000, 'subscription', 'debit', '影音会员'),
                (3000, 'refund', 'credit', '退款')]):
                billing_helpers.BillingGatewayTests.insert(c, dict(transaction_id=f'page_{i}', account_id='demo_account_a',
                    amount_minor=amount, kind=kind, direction=direction, category=category,
                    occurred_at='2026-08-31T15:59:59.999999Z'))
        self.app.number_input(key='bill_year').set_value(2026)
        self.app.selectbox(key='bill_month').select(8).run()

    def click(self, label):
        return next(b for b in self.app.button if b.label == label).click().run()

    def total(self):
        return [m.value for m in self.app.metric if m.label == '消费/订阅毛支出']

    def test_amount_table_chart_and_refund_agree(self):
        import streamlit
        with patch('streamlit.bar_chart', wraps=streamlit.bar_chart) as chart:
            self.click('查询账单')
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.total(), ['¥ 120.00'])
        table = next(d.value for d in self.app.dataframe if '金额（分）' in d.value.columns)
        self.assertEqual(table['金额（分）'].sum(), 12000)
        self.assertEqual(chart.call_args.args[0], table[['分类', '金额（分）']].to_dict('records'))
        self.assertTrue(any('退款入账 30.00 元' in m.value for m in self.app.markdown))
        self.assertTrue(any('空列表不代表没有异常' in m.value for m in self.app.info))

    def test_year_and_custom_dates_and_empty_month(self):
        self.app.radio(key='bill_mode').set_value('年份').run()
        self.click('查询账单')
        self.assertEqual(self.total(), ['¥ 120.00'])
        self.app.radio(key='bill_mode').set_value('日期范围').run()
        self.app.date_input(key='bill_start').set_value(date(2026,8,31))
        self.app.date_input(key='bill_end').set_value(date(2026,8,31)).run()
        self.click('查询账单')
        self.assertEqual(self.total(), ['¥ 120.00'])
        self.app.radio(key='bill_mode').set_value('月份').run()
        self.app.selectbox(key='bill_month').select(9).run()
        self.assertEqual(self.total(), [])
        self.click('查询账单')
        self.assertEqual(self.total(), ['¥ 0.00'])
        self.assertTrue(any('没有计入毛支出' in x.value for x in self.app.info))
        self.assertEqual(len(self.app.exception), 0)

    def test_failed_query_clears_previous_report(self):
        self.click('查询账单')
        execute = self.chat.gateway.execute
        def failing(request, **kwargs):
            if request['action'] == 'bill.analyze':
                raise ServiceError('UPSTREAM_ERROR', 'test failure')
            return execute(request, **kwargs)
        with patch.object(self.chat.gateway, 'execute', side_effect=failing):
            self.click('查询账单')
        self.assertEqual(self.total(), [])
        self.assertTrue(any('本次没有可展示的报告' in x.value for x in self.app.error))
        self.assertEqual(len(self.app.exception), 0)

    def test_bad_result_cannot_display_as_success(self):
        self.click('查询账单')
        execute = self.chat.gateway.execute
        def corrupt(request, **kwargs):
            result = execute(request, **kwargs)
            if request['action'] == 'bill.analyze':
                result['data']['total_expense_minor'] += 1
            return result
        with patch.object(self.chat.gateway, 'execute', side_effect=corrupt):
            self.click('查询账单')
        self.assertEqual(self.total(), [])
        self.assertFalse(any('金额（分）' in d.value.columns for d in self.app.dataframe))
        self.assertTrue(any('本次没有可展示的报告' in x.value for x in self.app.error))
        self.assertEqual(len(self.app.exception), 0)

    def test_leap_month_request_and_refresh_clear_report(self):
        self.app.number_input(key='bill_year').set_value(2028)
        self.app.selectbox(key='bill_month').select(2).run()
        with patch.object(self.chat.gateway, 'execute', wraps=self.chat.gateway.execute) as execute:
            self.click('查询账单')
        requests = [call.args[0] for call in execute.call_args_list if call.args[0]['action'] == 'bill.analyze']
        self.assertEqual(requests[0]['params'], dict(account_id='demo_account_a', start_date='2028-02-01', end_date='2028-02-29'))
        self.assertEqual(self.total(), ['¥ 0.00'])
        self.app.run()
        self.assertEqual(self.total(), [])
        self.assertEqual(len(self.app.exception), 0)

    def test_invalid_range_does_not_execute(self):
        self.app.radio(key='bill_mode').set_value('日期范围').run()
        self.app.date_input(key='bill_start').set_value(date(2026,9,1))
        self.app.date_input(key='bill_end').set_value(date(2026,8,1)).run()
        self.assertTrue(self.app.button(key='bill_query').disabled)
        self.assertEqual(self.total(), [])

    def test_account_selection_uses_owned_resources(self):
        with self.chat.gateway.store.connection(write=True) as c:
            c.execute("INSERT INTO accounts VALUES ('second','demo_user_a','第二账户','CNY',0,0,0,0)")
        self.app.run()
        options = self.app.selectbox(key='bill_account').options
        self.assertEqual(len(options), 2)
        self.assertFalse(any('李明' in label for label in options))
        self.app.selectbox(key='bill_account').select('second').run()
        self.click('查询账单')
        self.assertEqual(self.total(), ['¥ 0.00'])
        self.assertEqual(len(self.app.exception), 0)

    def test_query_preserves_pending_transfer(self):
        self.app.chat_input[0].set_value('给李明转200元').run()
        before = deepcopy((self.chat.request, self.chat.result))
        self.click('查询账单')
        self.assertEqual(before, (self.chat.request, self.chat.result))
        self.assertTrue(self.app.chat_input[0].disabled)
        self.click('确认此笔模拟转账')
        self.assertEqual(self.chat.result['status'], 'succeeded')
        self.assertEqual(self.total(), [])
        self.assertEqual(len(self.app.exception), 0)

    def test_unknown_transfer_stays_unknown_until_recovered(self):
        self.app.chat_input[0].set_value('给李明转200元').run()
        authorize = self.chat.gateway.authorize
        def lost(payload, **kwargs):
            authorize(payload, **kwargs)
            raise TimeoutError('lost')
        self.chat.gateway.authorize = lost
        self.click('确认此笔模拟转账')
        self.click('查询账单')
        self.assertEqual(self.chat.result['status'], 'unknown')
        self.assertTrue(self.app.chat_input[0].disabled)
        self.click('查询原操作结果')
        self.assertEqual(self.chat.result['status'], 'succeeded')
        self.assertEqual(len(self.app.exception), 0)


if __name__ == '__main__':
    unittest.main()
