"""V1 只读解析：保留来源，不推断业务关系、不执行公式、不输出正式流水。"""
from collections import Counter
from datetime import date, datetime, time
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from openpyxl import load_workbook


HEADERS = {
    '小账本': ('流水号', '日期', '月份', '摘要', '交易类型', '消费分类', '方向', '金额（元）',
               '账户', '账户编号', '收款人/关联方', '原交易关联编号', '备注'),
    '分类规则': ('交易类型', '适用消费分类', '判定规则', '方向', '典型示例', '边界与例外'),
    '账户资料': ('账户编号', '账户名称', '账户类型', '归属用户', '开户行/渠道', '尾号', '绑定关系', '用途说明'),
    '收款人资料': ('收款人编号', '收款人姓名', '账户尾号', '区分信息', '关联流水', '状态'),
    '字段说明': ('字段名', '含义', '单位/格式', '正负号', '必填', '编号关联', '示例', '备注'),
    '统计结果': ('月份', '毛支出（消费+订阅）', '资金流出合计', '总收入', '退款金额', '流水笔数'),
    '手算答案': ('月份', '交易类型', '消费分类', '纳入流水', '计算式', '金额（元）'),
    '异常样例': ('异常编号', '关联流水', '异常类型', '异常描述', '判断方式', '检测规则（阈值/范围/所需数据/预期结果）', '理由与核查建议'),
    '测试表': ('用例编号', '用例名称', '账户', '查询日期', '初始数据', '预期结果', '实际结果', '状态', '证据', '版本'),
    '协作材料清单': ('成员', '材料项', '状态', '版本', '负责人', '备注'),
}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_CELLS = 500_000
NARRATIVE_HEADERS = ('案例编号', '业务/规则编号', '前置数据', '输入与操作步骤',
                     '预期金额/状态/文案', '实际结果与证据', '版本/结论')
OPTIONAL_HEADERS = {'验收用例': tuple('场景' if h == '业务/规则编号' else h for h in NARRATIVE_HEADERS)}


class ReadError(ValueError):
    """输入无法安全读取；与资料中的结构问题分开。"""


def scalar(value):
    return value.isoformat() if isinstance(value, (datetime, date, time)) else value


def cell_record(cell, cached):
    return dict(cell=cell.coordinate, value=scalar(cell.value), data_type=cell.data_type,
                number_format=cell.number_format, cached_value=scalar(cached.value),
                cached_type=cached.data_type)


def parse_bytes(content):
    """解析固定字节快照。返回内部资料对象，不是 v1.1 transaction。

    sheets 保留所有非空单元格（包括公式/说明/额外表），ledger_rows 保留
    小账本数据行和缺字段行。issues 仅表示读取结构问题，非业务验收结论。
    """
    if not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES:
        raise ReadError('输入必须是 bytes，且文件不能超过 20 MiB。')
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(i.file_size for i in entries) > MAX_EXPANDED_BYTES:
                raise ReadError('工作簿解压规模超过读取上限。')
            if any(i.flag_bits & 1 for i in entries):
                raise ReadError('不支持加密工作簿。')
        # 同一份 bytes；关闭外部链接保留，不访问文件引用或网络。
        book = load_workbook(BytesIO(content), data_only=False, keep_links=False)
        try:
            cached = load_workbook(BytesIO(content), data_only=True, keep_links=False)
            try:
                return _extract(book, cached, content)
            finally:
                cached.close()
        finally:
            book.close()
    except ReadError:
        raise
    except Exception as exc:
        # 读取库对损坏 XML/ZIP/格式的异常类型不同；边界统一失败，不给部分成功。
        raise ReadError('无法读取 xlsx：文件可能损坏、加密或格式不受支持。') from exc


def _extract(book, cached, content):
    result = dict(source_sha256=sha256(content).hexdigest(), source_bytes=len(content),
                  sheets={}, ledger_rows=[], issues=[], notes=[])
    def issue(code, sheet, location, message):
        result['issues'].append(dict(code=code, sheet=sheet, location=location, message=message))
    size = 0
    for sheet in book:
        size += sheet.max_row * sheet.max_column
        if sheet.max_row > 20000 or sheet.max_column > 200 or size > MAX_CELLS:
            raise ReadError('工作表范围超过读取上限（20000 行、200 列、全表 500000 格）。')
        cells, by_row = [], {}
        for row in sheet:
            populated = [c for c in row if c.value is not None]
            if populated:
                by_row[row[0].row] = populated
                cells.extend(cell_record(c, cached[sheet.title][c.coordinate]) for c in populated)
        entry = dict(rows=sheet.max_row, columns=sheet.max_column, cells=cells, header_row=None, headers={})
        result['sheets'][sheet.title] = entry
        required = HEADERS.get(sheet.title, OPTIONAL_HEADERS.get(sheet.title))
        if required is None:
            continue
        variants = [required, NARRATIVE_HEADERS] if sheet.title == '测试表' else [required]
        choices = [(len({c.value.strip() for c in row if isinstance(c.value, str)} & set(schema)), r, schema)
                   for schema in variants for r, row in by_row.items()]
        scores = {}
        for score, r, schema in choices:
            scores[r] = max(scores.get(r, 0), score)
        best = max(scores.values(), default=0)
        candidates = [r for r, score in scores.items() if score == best and score >= 2]
        if len(candidates) != 1:
            issue('HEADER_UNRESOLVED', sheet.title, '', '未找到唯一表头，请检查缺失或重复表头。')
            continue
        header_row = candidates[0]
        schemas = [schema for score, r, schema in choices if r == header_row and score == best]
        if len(schemas) != 1:
            issue('HEADER_UNRESOLVED', sheet.title, str(header_row), '表头同时匹配多种模板，不能猜测列含义。')
            continue
        required = schemas[0]
        names = [c.value.strip() for c in by_row[header_row] if isinstance(c.value, str)]
        duplicates = [name for name, count in Counter(names).items() if count > 1]
        headers = {c.value.strip(): c.column for c in by_row[header_row] if isinstance(c.value, str)}
        entry.update(header_row=header_row, headers=headers)
        missing = sorted(set(required) - headers.keys())
        if duplicates:
            issue('DUPLICATE_HEADER', sheet.title, str(header_row), '重复表头：' + '、'.join(duplicates))
        if missing:
            issue('MISSING_COLUMN', sheet.title, str(header_row), '缺少列：' + '、'.join(missing))
        if sheet.title != '小账本' or duplicates or missing:
            continue
        for r, row in by_row.items():
            if r <= header_row:
                continue
            # 仅识别明确的单格说明；未知单格/残缺行也保留，交 V2 判定。
            first = row[0].value
            if len(row) == 1 and isinstance(first, str) and first.startswith(('说明：', '口径说明：', '交易类型（', '原交易关联编号：')):
                result['notes'].append(dict(sheet=sheet.title, row=r, cell=row[0].coordinate, value=first))
                continue
            fields = {name: cell_record(sheet.cell(r, headers[name]), cached[sheet.title].cell(r, headers[name]))
                      for name in required}
            result['ledger_rows'].append(dict(sheet=sheet.title, row=r, fields=fields))
    for name in HEADERS:
        if name not in result['sheets']:
            issue('MISSING_SHEET', name, '', '缺少工作表；其他可读表仍保留。')
    return result


def read_workbook(path):
    """只读路径；结果绑定第一次字节快照，结束时报告源文件是否变化。"""
    path = Path(path)
    try:
        with path.open('rb') as stream:
            content = stream.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise ReadError('无法打开资料文件。') from exc
    result = parse_bytes(content)
    result['source_path'] = str(path.absolute())
    try:
        with path.open('rb') as stream:
            final = stream.read(MAX_FILE_BYTES + 1)
        result['source_changed'] = sha256(final).hexdigest() != result['source_sha256']
    except OSError:
        result['source_changed'] = None
    return result
