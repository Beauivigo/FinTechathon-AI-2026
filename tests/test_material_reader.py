"""只读解析验收；内存合成的测试工作簿不写入成员原表。"""
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from openpyxl import Workbook
from material_check import reader


def workbook():
    book = Workbook()
    book.remove(book.active)
    for name, headers in reader.HEADERS.items():
        sheet = book.create_sheet(name)
        sheet.append(['测试标题'])
        sheet.append(list(headers))
    book['小账本'].append(['LX-001', datetime(2026, 8, 31), '=TEXT(B3,"yyyy-mm")', '消费',
                       '消费', '餐饮', '支出', -12.34, '测试账户', 'ACC-001', '—', '—', ''])
    return book


def contents(book):
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


class ReaderTests(unittest.TestCase):
    def test_source_values_types_and_positions_preserved(self):
        raw = contents(workbook())
        result = reader.parse_bytes(raw)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['source_sha256'], sha256(raw).hexdigest())
        row = result['ledger_rows'][0]
        self.assertEqual(row['row'], 3)
        fields = row['fields']
        self.assertEqual(fields['金额（元）']['value'], -12.34)
        self.assertEqual(fields['金额（元）']['cell'], 'H3')
        self.assertEqual(fields['日期']['value'], '2026-08-31T00:00:00')
        self.assertEqual(fields['日期']['data_type'], 'd')
        self.assertNotIn('occurred_at', row)
        self.assertEqual(fields['月份']['data_type'], 'f')
        self.assertIsNone(fields['月份']['cached_value'])

    def test_formula_and_error_cache_from_same_bytes(self):
        raw = contents(workbook())
        target = BytesIO()
        with ZipFile(BytesIO(raw)) as original, ZipFile(target, 'w') as output:
            for name in original.namelist():
                data = original.read(name)
                if name == 'xl/worksheets/sheet1.xml':
                    xml = ET.fromstring(data)
                    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
                    cell = next(c for c in xml.iter(ns+'c') if c.get('r') == 'C3')
                    cell.set('t', 'e')
                    cell.find(ns+'v').text = '#NAME?'
                    data = ET.tostring(xml)
                output.writestr(name, data)
        field = reader.parse_bytes(target.getvalue())['ledger_rows'][0]['fields']['月份']
        self.assertTrue(field['value'].startswith('=TEXT'))
        self.assertEqual(field['cached_value'], '#NAME?')
        self.assertEqual(field['cached_type'], 'e')

    def test_reordered_columns_and_added_rows(self):
        book = workbook()
        sheet = book['小账本']
        for r in (2, 3):
            sheet.cell(r, 1).value, sheet.cell(r, 8).value = sheet.cell(r, 8).value, sheet.cell(r, 1).value
        values = [c.value for c in sheet[3]]
        for i in range(25):
            row = list(values)
            row[7] = f'LX-{i+2:03d}'
            sheet.append(row)
        result = reader.parse_bytes(contents(book))
        self.assertEqual(len(result['ledger_rows']), 26)
        self.assertEqual(result['ledger_rows'][0]['fields']['金额（元）']['cell'], 'A3')

    def test_blank_notes_and_partial_rows_not_lost(self):
        book = workbook()
        sheet = book['小账本']
        sheet.append([])
        sheet.append(['口径说明：仅为测试'])
        sheet.append([None, datetime(2026, 9, 1)])
        sheet.append(['UNKNOWN SINGLE CELL'])
        result = reader.parse_bytes(contents(book))
        self.assertEqual(len(result['ledger_rows']), 3)
        self.assertEqual(len(result['notes']), 1)
        self.assertIsNone(result['ledger_rows'][1]['fields']['流水号']['value'])
        self.assertEqual(result['ledger_rows'][2]['fields']['流水号']['value'], 'UNKNOWN SINGLE CELL')

    def test_empty_ledger_with_header(self):
        book = workbook()
        book['小账本'].delete_rows(3)
        result = reader.parse_bytes(contents(book))
        self.assertEqual(result['ledger_rows'], [])
        self.assertEqual(result['issues'], [])

    def test_missing_sheet_preserves_others(self):
        book = workbook()
        book.remove(book['账户资料'])
        result = reader.parse_bytes(contents(book))
        self.assertEqual(result['issues'][0]['code'], 'MISSING_SHEET')
        self.assertEqual(len(result['ledger_rows']), 1)

    def test_missing_column_blocks_ledger_mapping_not_raw_cells(self):
        book = workbook()
        book['小账本']['H2'] = '未知金额标题'
        result = reader.parse_bytes(contents(book))
        self.assertEqual(result['ledger_rows'], [])
        self.assertEqual(result['issues'][0]['code'], 'MISSING_COLUMN')
        self.assertTrue(result['sheets']['小账本']['cells'])

    def test_duplicate_header_not_last_wins(self):
        book = workbook()
        book['小账本']['N2'] = '流水号'
        result = reader.parse_bytes(contents(book))
        self.assertEqual(result['issues'][0]['code'], 'DUPLICATE_HEADER')
        self.assertEqual(result['ledger_rows'], [])

    def test_repeated_header_is_ambiguous(self):
        book = workbook()
        book['小账本'].append(list(reader.HEADERS['小账本']))
        result = reader.parse_bytes(contents(book))
        self.assertEqual(result['issues'][0]['code'], 'HEADER_UNRESOLVED')
        self.assertEqual(result['ledger_rows'], [])

    def test_extra_sheet_preserved_without_blocking(self):
        book = workbook()
        book.create_sheet('额外说明').append(['保留此说明'])
        result = reader.parse_bytes(contents(book))
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['sheets']['额外说明']['cells'][0]['value'], '保留此说明')

    def test_bad_and_oversized_inputs_fail(self):
        for raw in (b'not an xlsx', b'', 'wrong type'):
            with self.subTest(raw=raw), self.assertRaises(reader.ReadError):
                reader.parse_bytes(raw)
        raw = contents(workbook())
        with patch.object(reader, 'MAX_FILE_BYTES', 10), self.assertRaises(reader.ReadError):
            reader.parse_bytes(raw)
        with patch.object(reader, 'MAX_CELLS', 2), self.assertRaises(reader.ReadError):
            reader.parse_bytes(raw)

    def test_readonly_file_and_stable_result(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'test.xlsx'
            raw = contents(workbook())
            path.write_bytes(raw)
            first = reader.read_workbook(path)
            self.assertEqual(first, reader.read_workbook(path))
            self.assertFalse(first['source_changed'])
            self.assertEqual(path.read_bytes(), raw)

    def test_changed_file_result_keeps_original_snapshot(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'test.xlsx'
            raw = contents(workbook())
            path.write_bytes(raw)
            parse = reader.parse_bytes
            def changed(content):
                path.write_bytes(b'new file')
                return parse(content)
            with patch.object(reader, 'parse_bytes', side_effect=changed):
                result = reader.read_workbook(path)
            self.assertTrue(result['source_changed'])
            self.assertEqual(result['source_sha256'], sha256(raw).hexdigest())
            self.assertEqual(len(result['ledger_rows']), 1)

    def test_missing_input_has_clear_error(self):
        with TemporaryDirectory() as directory, self.assertRaises(reader.ReadError):
            reader.read_workbook(Path(directory) / 'missing.xlsx')


if __name__ == '__main__':
    unittest.main()
