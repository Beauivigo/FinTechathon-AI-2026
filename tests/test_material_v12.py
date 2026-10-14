"""合成 V1.2 多表区/叙述模板；不使用成员原表作为可写测试输入。"""
import unittest
from material_check.reader import NARRATIVE_HEADERS, OPTIONAL_HEADERS, parse_bytes
from material_check.report import build_report, compare_reports
from tests.test_material_reconcile import fixture
from tests.test_material_reader import contents


def v12():
    book = fixture()
    sheet = book['统计结果']
    sheet.append(['分类金额'])
    sheet.append(['交易类型','2026-08','2026-09','合计','口径说明'])
    sheet.append(['消费',-12.34,0,-12.34])
    sheet.append(['分单位对照'])
    sheet.append(['期间','毛支出（分）','仓库预期（分）','毛支出核对','退款（分）','退款核对'])
    sheet.append(['2026-08',3234,3234,'一致',3000,3000])
    sheet.append(['合计',3234,3234,'一致',3000,3000])
    sheet.append(['后续其他区域'])
    sheet.append(['2026-08',999,999])
    sheet = book['测试表']
    sheet.delete_rows(1,sheet.max_row)
    sheet.append(list(NARRATIVE_HEADERS))
    sheet.append(['TC-001','C2','ACC-001 2026-08','查账单','毛支出32.34',None,'未执行'])
    sheet.append(['说明：尾部说明'])
    sheet = book.create_sheet('验收用例')
    sheet.append(list(OPTIONAL_HEADERS['验收用例']))
    sheet.append(['UC-101','转账','待 B 确认','转账','成功',None,'通过'])
    book['账户资料'].append(['映射状态（V1.2 修正）：C 草稿未批准'])
    return book


class V12Tests(unittest.TestCase):
    def test_sections_preserve_fen_without_second_conversion(self):
        report = build_report(parse_bytes(contents(v12())))
        comparisons = report['result']['comparisons']
        stats = [c for c in comparisons if c['sheet']=='统计结果']
        self.assertEqual(len(stats),16)
        self.assertTrue(all(c['status']=='match' for c in stats))
        self.assertEqual(sum(c['source_unit']=='fen' for c in stats),8)
        self.assertEqual(report['result']['totals']['gross_minor'],3234)
        self.assertFalse(report['result']['unperformed_comparisons'])

    def test_narrative_and_optional_cases_preserve_evidence_not_auto_approve(self):
        report = build_report(parse_bytes(contents(v12())))
        cases = report['result']['test_scope_checks']
        self.assertEqual([c['case_id'] for c in cases],['TC-001','UC-101'])
        self.assertTrue(all(c['status']=='not_executed' and c['source_account'] is None for c in cases))
        self.assertEqual(cases[1]['source_reported_status'],'通过')
        self.assertEqual(cases[0]['source_fields']['前置数据']['value'],'ACC-001 2026-08')
        self.assertFalse(report['material_ready'])

    def test_mapping_note_is_retained_but_not_an_account(self):
        parsed = parse_bytes(contents(v12()))
        report = build_report(parsed)
        self.assertTrue(any(str(c['value']).startswith('映射状态（') for c in parsed['sheets']['账户资料']['cells']))
        self.assertFalse(any(i['code']=='INVALID_ID' and i['sheet']=='账户资料' for i in report['issues']))
        book = v12()
        book['账户资料'].append(['unfinished-account'])
        report = build_report(parse_bytes(contents(book)))
        self.assertTrue(any(i['code']=='OWNER_MISSING' for i in report['issues']))

    def test_column_reorder_and_inserted_rows(self):
        book = v12()
        for name in ('统计结果','测试表','验收用例'):
            sheet = book[name]
            values = [[cell.value for cell in row] for row in sheet]
            sheet.delete_rows(1,sheet.max_row)
            sheet.append(['新标题'])
            for row in values:
                sheet.append(list(reversed(row)))
        report = build_report(parse_bytes(contents(book)))
        self.assertEqual(len(report['result']['test_scope_checks']),2)
        self.assertTrue(all(c['status']=='match' for c in report['result']['comparisons'] if c['sheet']=='统计结果'))

    def test_bad_fen_is_unavailable_not_rounded(self):
        for value in (3234.5,True,'bad',9007199254740992):
            with self.subTest(value=value):
                book=v12();book['统计结果']['B10']=value
                report=build_report(parse_bytes(contents(book)))
                item=next(c for c in report['result']['comparisons'] if c['cell']=='B10' and c['sheet']=='统计结果')
                self.assertEqual(item['status'],'unavailable')

    def test_wrong_fen_mismatch_does_not_change_total(self):
        book=v12();book['统计结果']['B10']=999
        report=build_report(parse_bytes(contents(book)))
        self.assertEqual(report['result']['totals']['gross_minor'],3234)
        self.assertTrue(any(c['status']=='mismatch' for c in report['result']['comparisons']))

    def test_missing_or_duplicate_fen_headers_skip_that_region(self):
        for change in ('missing','duplicate'):
            book=v12();book['统计结果']['F9']=None if change=='missing' else '退款（分）'
            report=build_report(parse_bytes(contents(book)))
            self.assertTrue(any('分单位对照表缺列' in s for s in report['result']['unperformed_comparisons']))
            self.assertEqual(sum(c['sheet']=='统计结果' for c in report['result']['comparisons']),8)

    def test_duplicate_narrative_headers_not_guessed(self):
        book=v12();book['测试表']['G1']='前置数据'
        parsed=parse_bytes(contents(book))
        self.assertIn('DUPLICATE_HEADER',[i['code'] for i in parsed['issues']])
        report=build_report(parsed)
        self.assertEqual([c['case_id'] for c in report['result']['test_scope_checks']],['UC-101'])

    def test_old_layout_remains_supported(self):
        book=fixture();book['测试表'].append(['TC-001','旧版','全账户','2026-08','数据','预期',None,'待执行',None,'1.1'])
        report=build_report(parse_bytes(contents(book)))
        self.assertEqual(report['result']['test_scope_checks'][0]['source_account'],'全账户')
        self.assertEqual(sum(c['sheet']=='统计结果' for c in report['result']['comparisons']),8)

    def test_previous_parser_report_requires_regeneration(self):
        report=build_report(parse_bytes(contents(v12())))
        with self.assertRaises(ValueError):
            compare_reports(dict(report,schema_version='material-report/1'),report)


if __name__=='__main__':
    unittest.main()
