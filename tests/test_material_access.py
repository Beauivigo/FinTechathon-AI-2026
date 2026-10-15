from copy import deepcopy
from datetime import date
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from material_check import approved
from material_check.reader import parse_bytes
from shared import ServiceError
from tests.test_material_reader import workbook, contents
from tests.test_app import AppTest


class MaterialAccessTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        book = workbook()
        book['小账本']['C3'] = '2026-08'
        book['账户资料'].append(['ACC-001','模拟账户','储蓄账户','用户C','模拟银行','1234','—','测试'])
        book['分类规则'].append(['消费','餐饮','规则','支出','样例','说明'])
        self.raw = contents(book)
        self.source = self.root/'C.xlsx'
        self.source.write_bytes(self.raw)
        decision = self.root/'B.xlsx'
        decision.write_bytes(b'approved-test-fixture')
        for name, value in [('SOURCE',self.source), ('DECISION',decision),
                            ('SOURCE_SHA',sha256(self.raw).hexdigest()),
                            ('DECISION_SHA',sha256(decision.read_bytes()).hexdigest())]:
            p = patch.object(approved,name,value); p.start(); self.addCleanup(p.stop)
        self.params = dict(account_id='ACCT-C001', start_date='2026-08-01', end_date='2026-08-31')

    def query(self, **changes):
        return approved.query(self.params | changes, principal_id=approved.PRINCIPAL)

    def test_readonly_result_provenance_and_empty_account(self):
        result = self.query()
        self.assertEqual(result['bill']['total_expense_minor'],1234)
        self.assertEqual(result['transactions'][0]['occurred_at'],'2026-08-31T00:00:00+08:00')
        self.assertEqual(result['provenance']['LX-001']['date_precision'],'day')
        self.assertEqual(self.query(account_id='ACCT-C003')['transactions'],[])
        self.assertEqual(self.source.read_bytes(),self.raw)
        self.assertEqual(set(p.name for p in self.root.iterdir()),{'B.xlsx','C.xlsx'})

    def test_authorization_before_file_read(self):
        with patch.object(approved,'read_workbook') as read:
            for principal, aid in [('demo_user_a','ACCT-C001'),('PRIN-C01','demo_account_a')]:
                with self.assertRaises(ServiceError) as ctx:
                    approved.query(self.params | {'account_id':aid},principal_id=principal)
                self.assertEqual(ctx.exception.code,'FORBIDDEN')
            read.assert_not_called()

    def test_changed_or_missing_source_fails_closed(self):
        self.source.write_bytes(self.raw+b'changed')
        with self.assertRaises(ServiceError): self.query()
        self.source.unlink()
        with self.assertRaises(ServiceError): self.query()

    def test_changed_decision_and_reversed_dates(self):
        approved.DECISION.write_bytes(b'new rules')
        with self.assertRaises(ServiceError): self.query()
        with self.assertRaises(ServiceError) as ctx: self.query(end_date='2026-07-31')
        self.assertEqual(ctx.exception.code,'INVALID_REQUEST')

    def test_bad_row_never_produces_partial_bill(self):
        parsed = parse_bytes(self.raw)
        for field, value in [('金额（元）',-0.001),('金额（元）',12.34),('账户编号','CH-unknown'),('消费分类','未知')]:
            bad = deepcopy(parsed)
            bad['ledger_rows'][0]['fields'][field]['value'] = value
            with self.assertRaises(ServiceError): approved._convert(bad)
        bad = deepcopy(parsed); bad['ledger_rows'].append(deepcopy(bad['ledger_rows'][0]))
        with self.assertRaises(ServiceError): approved._convert(bad)

    @unittest.skipIf(AppTest is None,'页面依赖未安装')
    def test_page_query_clears_stale_results_and_never_opens_bank(self):
        with patch('bank.store.Store.initialize',side_effect=AssertionError('must not touch bank')):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app/pages/1_资料账单.py')).run()
            app.button(key='material_query').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value,'12.34')
            app.date_input(key='material_end').set_value(date(2026,8,30)).run()
            self.assertFalse(app.metric)
            self.source.unlink()
            app.button(key='material_query').click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.assertFalse(app.metric)


if __name__ == '__main__': unittest.main()
