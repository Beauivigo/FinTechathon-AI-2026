"""V2 检查逻辑；只用合成资料，不写成员原表。"""
from copy import deepcopy
import unittest

from material_check.checks import check_material, REVIEWED_SHA
from material_check.reader import parse_bytes
from tests.test_material_reader import workbook, contents


class MaterialChecksTests(unittest.TestCase):
    def setUp(self):
        book = workbook()
        book['小账本']['C3'] = '2026-08'
        book['账户资料'].append(['ACC-001','测试账户','储蓄账户','测试用户','测试银行','1234','—','测试'])
        book['分类规则'].append(['消费','餐饮/交通','规则','支出','样例','说明'])
        self.parsed = parse_bytes(contents(book))

    def set_value(self, name, value, data_type=None):
        cell = self.parsed['ledger_rows'][0]['fields'][name]
        cell['value'] = value
        cell['cached_value'] = value
        if data_type:
            cell['data_type'] = data_type

    def codes(self):
        return {i['code'] for i in check_material(self.parsed)['issues']}

    def test_clean_fields_not_equated_with_import_approval(self):
        before = deepcopy(self.parsed)
        result = check_material(self.parsed)
        self.assertEqual(result['counts'].get('error', 0), 0)
        self.assertFalse(result['import_ready'])
        self.assertIn('MAPPING_NOT_APPROVED', self.codes())
        self.assertEqual(self.parsed, before)
        self.assertEqual(result, check_material(self.parsed))

    def test_amount_precision_range_and_types(self):
        for value in (True, None, 0, float('nan'), float('inf'), '0.001', '90071992547409.92', 'abc'):
            with self.subTest(value=value):
                self.set_value('金额（元）', value)
                self.assertIn('INVALID_AMOUNT', self.codes())
        for value in ('-90071992547409.91', '-0.01', '-12.34'):
            self.set_value('金额（元）', value)
            self.assertNotIn('INVALID_AMOUNT', self.codes())

    def test_sign_direction_kind_and_category(self):
        self.set_value('金额（元）', 12.34)
        self.assertIn('AMOUNT_DIRECTION', self.codes())
        self.set_value('方向', '收入')
        self.assertIn('KIND_DIRECTION', self.codes())
        self.set_value('消费分类', '不在字典')
        self.assertIn('CATEGORY_UNKNOWN', self.codes())
        self.set_value('消费分类', '—')
        self.assertIn('CATEGORY_MISSING', self.codes())
        self.set_value('交易类型', '猜测类型')
        self.assertIn('UNKNOWN_KIND', self.codes())

    def test_duplicate_and_invalid_ids(self):
        self.parsed['ledger_rows'].append(deepcopy(self.parsed['ledger_rows'][0]))
        self.assertIn('DUPLICATE_ID', self.codes())
        self.set_value('流水号', 'bad id')
        self.assertIn('INVALID_ID', self.codes())

    def test_date_month_and_unconfirmed_time(self):
        self.set_value('日期', '2026-02-30', 's')
        self.assertIn('INVALID_DATE', self.codes())
        self.set_value('日期', '2026-08-31', 's')
        self.set_value('月份', '2026-09')
        self.assertIn('MONTH_MISMATCH', self.codes())
        self.set_value('日期', '2026-08-31T12:00:00', 'd')
        self.assertIn('TIME_UNCONFIRMED', self.codes())

    def test_bad_account_and_missing_reference_table(self):
        self.set_value('账户编号', 'ACC-missing')
        self.assertIn('ACCOUNT_REFERENCE', self.codes())
        self.parsed['issues'].append(dict(code='MISSING_SHEET', sheet='账户资料', location='', message='缺表'))
        self.parsed['sheets'].pop('账户资料')
        result = check_material(self.parsed)
        self.assertNotIn('ACCOUNT_REFERENCE', {i['code'] for i in result['issues']})
        self.assertTrue(any(i['sheet']=='账户资料' for i in result['skipped_checks']))

    def test_channel_cycle_and_duplicate_account(self):
        book = workbook()
        book['账户资料'].append(['CH-001','微信','支付渠道','用户','渠道','—','绑定 CH-002','测试'])
        book['账户资料'].append(['CH-002','微信','支付渠道','用户','渠道','—','绑定 CH-001','测试'])
        result = check_material(parse_bytes(contents(book)))
        self.assertEqual(sum(i['code']=='CHANNEL_CHAIN' for i in result['issues']), 2)
        book = workbook()
        for _ in range(2):
            book['账户资料'].append(['ACC-001','账户','储蓄账户','用户','银行','—','—','测试'])
        result = check_material(parse_bytes(contents(book)))
        self.assertIn('DUPLICATE_ID', {i['code'] for i in result['issues']})
        self.assertIn('ACCOUNT_REFERENCE', {i['code'] for i in result['issues']})

    def test_account_explanation_not_treated_as_identifier(self):
        book = workbook()
        book['账户资料'].append(['支付渠道不等于账户：这是说明'])
        result = check_material(parse_bytes(contents(book)))
        self.assertFalse(any(i['code']=='INVALID_ID' and i['sheet']=='账户资料' for i in result['issues']))

    def test_refund_original_missing_is_pending_not_invented(self):
        self.set_value('交易类型', '退款')
        self.set_value('方向', '收入')
        self.set_value('金额（元）', 30)
        self.set_value('原交易关联编号', '外部待补')
        result = check_material(self.parsed)
        item = next(i for i in result['issues'] if i['code']=='ORIGINAL_PENDING')
        self.assertEqual(item['level'], 'needs_decision')
        self.assertEqual(item['cell'], 'L3')
        self.assertIn('PARTY_MISSING', self.codes())

    def test_payee_name_ambiguity_requires_decision(self):
        book = workbook()
        book['小账本']['E3'] = '转账'
        book['小账本']['K3'] = '小王'
        for i in range(2):
            book['收款人资料'].append([f'PAYEE-{i}', '小王', str(i), '区分', 'LX-001', '待确认'])
        result = check_material(parse_bytes(contents(book)))
        self.assertIn('PAYEE_UNRESOLVED', {i['code'] for i in result['issues']})

    def test_payee_references_missing_ledger(self):
        book = workbook()
        book['收款人资料'].append(['PAYEE-1', '小王', '1234', '说明', 'LX-999', '待确认'])
        result = check_material(parse_bytes(contents(book)))
        self.assertIn('PAYEE_LEDGER_REFERENCE', {i['code'] for i in result['issues']})

    def test_formula_errors_and_missing_cache(self):
        cell = self.parsed['sheets']['字段说明']['cells'][0]
        cell.update(data_type='f', value='=bad', cached_type='e', cached_value='#NAME?')
        self.assertIn('EXCEL_ERROR', self.codes())
        cell.update(cached_type='n', cached_value=None)
        self.assertIn('CACHE_MISSING', self.codes())
        self.set_value('金额（元）', '=1+1', 'f')
        self.assertIn('SOURCE_FORMULA', self.codes())

    def test_structure_failure_records_skipped_not_empty_success(self):
        self.parsed['issues'].append(dict(code='DUPLICATE_HEADER', sheet='小账本', location='2', message='重复表头'))
        result = check_material(self.parsed)
        self.assertEqual(result['checked_ledger_rows'], 0)
        self.assertTrue(result['skipped_checks'])

    def test_known_conflict_only_applies_to_reviewed_version(self):
        self.assertNotIn('KNOWN_PARTY_CONFLICT', self.codes())
        self.parsed['source_sha256'] = REVIEWED_SHA
        self.assertIn('KNOWN_PARTY_CONFLICT', self.codes())
        self.parsed['source_changed'] = True
        self.assertIn('SOURCE_CHANGED', self.codes())


if __name__ == '__main__':
    unittest.main()
