"""V4 使用合成工作簿验证重跑、问题比较和报告写入失败。"""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from material_check.__main__ import main
from material_check.reader import parse_bytes
from material_check.report import build_report, compare_reports, markdown
from tests.test_material_reconcile import fixture
from tests.test_material_reader import contents


def report(book):
    return build_report(parse_bytes(contents(book)))


class ReportTests(unittest.TestCase):
    def test_same_input_same_ids_and_markdown_facts(self):
        book = fixture()
        first, second = report(book), report(book)
        delta = compare_reports(first, second)
        self.assertEqual(len(delta['continued']), len(first['issues']))
        self.assertFalse(delta['appeared'] or delta['disappeared'])
        rendered = markdown(first)
        self.assertIn(first['source']['sha256'], rendered)
        self.assertIn('3234', rendered)
        for issue in first['issues']:
            self.assertIn(issue['issue_id'], rendered)

    def test_row_move_does_not_change_ledger_problem_id(self):
        book = fixture()
        book['小账本']['H3'] = 'bad'
        before = report(book)
        book['小账本'].insert_rows(3)
        after = report(book)
        old = next(i for i in before['issues'] if i['code']=='INVALID_AMOUNT')
        new = next(i for i in after['issues'] if i['code']=='INVALID_AMOUNT')
        self.assertEqual(old['issue_id'], new['issue_id'])
        self.assertNotEqual(old['cell'], new['cell'])
        self.assertIn(new, compare_reports(before, after)['continued'])

    def test_fixed_value_disappears_but_missing_table_does_not(self):
        book = fixture()
        book['小账本']['H3'] = 'bad'
        bad = report(book)
        fixed = report(fixture())
        self.assertIn('INVALID_AMOUNT', [i['code'] for i in compare_reports(bad,fixed)['disappeared']])
        del book['小账本']
        delta = compare_reports(bad, report(book))
        self.assertNotIn('INVALID_AMOUNT', [i['code'] for i in delta['disappeared']])
        self.assertIn('INVALID_AMOUNT', [i['code'] for i in delta['unassessed_previous']])

    def test_new_error_and_deleted_record_are_not_confused(self):
        book = fixture()
        good = report(book)
        book['小账本']['H3'] = 'bad'
        bad = report(book)
        self.assertIn('INVALID_AMOUNT', [i['code'] for i in compare_reports(good,bad)['appeared']])
        book['小账本'].delete_rows(3)
        self.assertIn('INVALID_AMOUNT', [i['code'] for i in compare_reports(bad,report(book))['unassessed_previous']])

    def test_changed_source_and_version_specific_issue_never_claim_fixed(self):
        parsed = parse_bytes(contents(fixture()))
        old = build_report(parsed)
        parsed['source_changed'] = True
        changed = build_report(parsed)
        self.assertFalse(changed['coverage']['comparison_targets'])
        self.assertFalse(changed['coverage']['ledger_recheck_safe'])
        injected = deepcopy(old)
        injected['issues'][0]['code'] = 'KNOWN_PARTY_CONFLICT'
        injected['issues'][0]['issue_id'] = 'version-specific'
        self.assertTrue(compare_reports(injected,old)['unassessed_previous'])

    def test_incompatible_or_failed_report_rejected(self):
        current = report(fixture())
        for field, value in [('schema_version','old'),('tool_status','failed')]:
            previous = dict(current, **{field:value})
            with self.assertRaises(ValueError):
                compare_reports(previous,current)

    def test_markdown_does_not_render_source_html(self):
        current = report(fixture())
        current['issues'][0]['observation'] = '<script>x</script>|a\nb'
        rendered = markdown(current)
        self.assertNotIn('<script>',rendered)
        self.assertIn('&#124;',rendered)

    def test_cli_readonly_reports_repeat_comparison_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            root = Path(directory)
            source = root/'source.xlsx'
            data = contents(fixture())
            source.write_bytes(data)
            args = ['--input',str(source),'--output-dir',str(root/'one')]
            self.assertEqual(main(args),1)
            saved = (root/'one/report.json').read_bytes()
            self.assertEqual(main(args),2)
            self.assertEqual((root/'one/report.json').read_bytes(),saved)
            self.assertEqual(main(['--input',str(source),'--output-dir',str(root/'two'),
                                   '--previous',str(root/'one/report.json')]),1)
            second = json.loads((root/'two/report.json').read_text(encoding='utf-8'))
            self.assertFalse(second['changes']['appeared'])
            self.assertEqual(source.read_bytes(),data)

    def test_read_and_write_failure_leave_no_valid_new_report(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            root = Path(directory)
            args = ['--input',str(root/'source.xlsx'),'--output-dir',str(root/'output')]
            self.assertEqual(main(args),2)
            self.assertFalse((root/'output').exists())
            (root/'source.xlsx').write_bytes(contents(fixture()))
            original = Path.open
            def fail_md(path, *a, **kw):
                if path.name=='report.md':
                    raise OSError('模拟报告写入失败')
                return original(path,*a,**kw)
            with patch.object(Path,'open',fail_md):
                self.assertEqual(main(args),2)
            self.assertFalse((root/'output/report.json').exists())
            self.assertFalse((root/'output/report.md').exists())

    def test_runtime_output_rejected(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--input','missing.xlsx','--output-dir','runtime/material-report']),2)

    def test_changed_transaction_type_is_not_reported_as_fixed_party(self):
        book = fixture()
        book['小账本']['K5'] = None
        old = report(book)
        book['小账本']['E5'] = '收入'
        delta = compare_reports(old, report(book))
        self.assertIn('PARTY_MISSING', [i['code'] for i in delta['unassessed_previous']])
        self.assertNotIn('PARTY_MISSING', [i['code'] for i in delta['disappeared']])

    def test_cli_wrong_previous_json_shape_fails_without_report(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()):
            root = Path(directory)
            source = root/'source.xlsx'
            source.write_bytes(contents(fixture()))
            previous = root/'previous.json'
            for value in ([], None, {'schema_version':'material-report/1','tool_status':'completed','issues':[None]}):
                previous.write_text(json.dumps(value),encoding='utf-8')
                self.assertEqual(main(['--input',str(source),'--output-dir',str(root/'out'),
                                       '--previous',str(previous)]),2)
                self.assertFalse((root/'out').exists())

    def test_cli_source_change_during_reconciliation_is_reported(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            root = Path(directory)
            source = root/'source.xlsx'
            source.write_bytes(contents(fixture()))
            def changing(parsed):
                result = build_report(parsed)
                source.write_bytes(b'changed by external editor')
                return result
            with patch('material_check.__main__.build_report', side_effect=changing):
                self.assertEqual(main(['--input',str(source),'--output-dir',str(root/'out')]),1)
            saved = json.loads((root/'out/report.json').read_text(encoding='utf-8'))
            self.assertTrue(saved['source']['changed'])
            self.assertIn('SOURCE_CHANGED',[i['code'] for i in saved['issues']])


if __name__ == '__main__':
    unittest.main()
