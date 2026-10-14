"""V3 独立复算与预览边界；工作簿均为合成测试输入。"""
from copy import deepcopy
import unittest

from material_check.reader import parse_bytes
from material_check.reconcile import reconcile_material
from tests.test_material_reader import workbook, contents


def fixture():
    book = workbook()
    book['小账本']['C3'] = '2026-08'
    book['分类规则'].append(['消费','餐饮','规则','支出','样例','说明'])
    book['分类规则'].append(['订阅','影音会员','规则','支出','样例','说明'])
    base = [c.value for c in book['小账本'][3]]
    for tid, kind, category, direction, amount in [
        ('LX-002','订阅','影音会员','支出',-20), ('LX-003','退款','—','收入',30),
        ('LX-004','转账','—','支出',-100), ('LX-005','理财','—','支出',-500),
        ('LX-006','收入','—','收入',1000)]:
        row = list(base)
        row[0],row[4],row[5],row[6],row[7] = tid,kind,category,direction,amount
        book['小账本'].append(row)
    book['统计结果'].append(['2026-08',32.34,632.34,1030,30,6])
    book['统计结果'].append(['合计',32.34,632.34,1030,30,6])
    book['手算答案'].append(['2026-08','消费','餐饮','LX-001','-12.34',-12.34])
    book['手算答案'].append(['2026-08','订阅','影音会员','LX-002','-20',-20])
    book['手算答案'].append(['2026-08 小计（毛支出）','—','—','LX-001/002','-12.34-20',-32.34])
    book['手算答案'].append(['合计（毛支出）','—','—','全部','-32.34',-32.34])
    return book


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_bytes(contents(fixture()))

    def test_gross_refund_and_cashflow_separate(self):
        result = reconcile_material(self.parsed)
        self.assertTrue(result['arithmetic_complete'])
        self.assertFalse(result['material_ready'])
        self.assertEqual(result['totals'], dict(gross_minor=3234,refund_minor=3000,
                                              inflow_minor=103000,outflow_minor=63234,count=6))
        self.assertEqual(result['valid_row_subtotals']['2026-08']['categories'], {'餐饮':1234,'影音会员':2000})
        self.assertEqual(sum(c['status']=='sign_mismatch' for c in result['comparisons']),4)

    def test_no_input_mutation_or_formal_transaction_preview(self):
        before = deepcopy(self.parsed)
        result = reconcile_material(self.parsed)
        self.assertEqual(self.parsed,before)
        for item in result['preview']:
            self.assertNotIn('account_id',item['candidate'])
            self.assertNotIn('occurred_at',item['candidate'])
            self.assertTrue(item['unresolved'])

    def test_bad_amount_blocks_full_total_and_marks_subtotal(self):
        self.parsed['ledger_rows'][0]['fields']['金额（元）']['value']='bad'
        result = reconcile_material(self.parsed)
        self.assertFalse(result['arithmetic_complete'])
        self.assertIsNone(result['totals'])
        self.assertEqual(result['valid_row_subtotals']['2026-08']['gross_minor'],2000)
        self.assertIn('不完整',result['subtotal_label'])
        self.assertTrue(all(c['status']=='unavailable' for c in result['comparisons']))

    def test_duplicates_not_silently_deduplicated(self):
        self.parsed['ledger_rows'].append(deepcopy(self.parsed['ledger_rows'][0]))
        result = reconcile_material(self.parsed)
        self.assertEqual(len(result['excluded_rows']),2)
        self.assertIsNone(result['totals'])

    def test_month_recomputed_from_date_not_cache(self):
        self.parsed['ledger_rows'][0]['fields']['月份']['value']='2026-09'
        result = reconcile_material(self.parsed)
        self.assertEqual(result['totals']['gross_minor'],3234)
        self.assertIn('MONTH_MISMATCH',{i['code'] for i in result['validation']['issues']})

    def test_source_formula_not_treated_as_confirmed_cash(self):
        cell=self.parsed['ledger_rows'][0]['fields']['金额（元）']
        cell.update(value='=-12.34',data_type='f',cached_value=-12.34)
        result=reconcile_material(self.parsed)
        self.assertIsNone(result['totals'])
        self.assertNotIn('amount_minor',result['preview'][0]['candidate'])

    def test_summary_mismatch_does_not_change_computed_total(self):
        book=fixture()
        book['统计结果']['B3']=999
        result=reconcile_material(parse_bytes(contents(book)))
        self.assertEqual(result['totals']['gross_minor'],3234)
        comparison=next(c for c in result['comparisons'] if c['sheet']=='统计结果' and c['cell']=='B3')
        self.assertEqual(comparison['status'],'mismatch')

    def test_hand_ids_and_different_section(self):
        book=fixture()
        book['手算答案']['D3']='LX-999'
        book['手算答案'].append(['月份','纳入流水','计算式','金额（元）'])
        book['手算答案'].append(['2026-08','LX-001','12.34',12.34])
        result=reconcile_material(parse_bytes(contents(book)))
        comparison=next(c for c in result['comparisons'] if c['sheet']=='手算答案' and c['cell']=='D3')
        self.assertEqual(comparison['status'],'mismatch')
        self.assertFalse(any(c['cell']=='F8' and c['sheet']=='手算答案' for c in result['comparisons']))

    def test_overflow_never_returns_unsafe_totals(self):
        self.parsed['ledger_rows'][0]['fields']['金额（元）']['value']='-90071992547409.91'
        result=reconcile_material(self.parsed)
        self.assertTrue(result['overflow'])
        self.assertIsNone(result['totals'])
        self.assertIsNone(result['valid_row_subtotals'])

    def test_missing_ledger_is_not_zero_success(self):
        self.parsed['issues'].append(dict(code='MISSING_COLUMN',sheet='小账本',location='2',message='缺列'))
        self.parsed['ledger_rows']=[]
        result=reconcile_material(self.parsed)
        self.assertIsNone(result['totals'])
        self.assertFalse(result['arithmetic_complete'])

    def test_missing_hand_category_is_unperformed(self):
        book=fixture()
        book['手算答案'].delete_rows(4)
        result=reconcile_material(parse_bytes(contents(book)))
        self.assertIn('手算缺少分类：2026-08 影音会员',result['unperformed_comparisons'])

    def test_case_scope_is_not_marked_executed(self):
        book=fixture()
        book['测试表'].append(['TC-001','整体复核','全账户','2026-08','数据','答案',None,'待执行',None,'测试'])
        result=reconcile_material(parse_bytes(contents(book)))
        self.assertEqual(result['test_scope_checks'][0]['status'],'not_executed')
        self.assertIn('不是单账户',result['test_scope_checks'][0]['reason'])


if __name__=='__main__':
    unittest.main()
