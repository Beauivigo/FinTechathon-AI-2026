"""V2 离线资料检查，不改原值，不批准映射，不产生正式业务请求。"""
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re

from openpyxl.utils.cell import coordinate_from_string, column_index_from_string, get_column_letter
from shared.contracts import ContractError, MAX_SAFE_INTEGER, check_value


KINDS = {'收入': 'income', '消费': 'consumption', '转账': 'transfer',
         '退款': 'refund', '理财': 'wealth', '订阅': 'subscription'}
REVIEWED_SHA = 'f41c74ec6b7121770352d8b966bc634b3dbe3bc8958830d06704fd9eebbb0127'


def empty(value):
    return value is None or (isinstance(value, str) and value.strip() in {'', '—', '-'})


def table_rows(parsed, name):
    """只在表头结构可靠时按名称取数据；不把备注当账户记录。"""
    sheet = parsed['sheets'].get(name)
    if not sheet or sheet['header_row'] is None or any(i['sheet'] == name for i in parsed['issues']):
        return None
    rows = {}
    for cell in sheet['cells']:
        col, row = coordinate_from_string(cell['cell'])
        if row > sheet['header_row']:
            rows.setdefault(row, {})[column_index_from_string(col)] = cell
    result = []
    for row, cells in sorted(rows.items()):
        if len(cells) == 1 and str(next(iter(cells.values()))['value']).startswith(('说明：', '支付渠道不等于账户：')):
            continue
        fields = {name: cells.get(col, dict(cell=f'{get_column_letter(col)}{row}', value=None, data_type='n'))
                  for name, col in sheet['headers'].items()}
        result.append(fields)
    return result


def check_material(parsed):
    """返回诊断清单及检查覆盖。只接受 V1 解析结果，不宣称可导入。"""
    issues, skipped = [], []
    def add(code, level, sheet, cell, message, impact, blocker='C1-001', record_id=None):
        issues.append(dict(code=code, level=level, sheet=sheet, cell=cell, record_id=record_id,
                           message=message, impact=impact, blocker=blocker))
    for item in parsed['issues']:
        add(item['code'], 'error', item['sheet'], item['location'], item['message'], '该表依赖检查不能完成')
    if parsed.get('source_changed') is not False and 'source_changed' in parsed:
        add('SOURCE_CHANGED', 'needs_decision', '', '', '源文件已变化或结束时无法确认，结果仅对应读取指纹。', '重新读取后才能对当前文件下结论')
    for name, sheet in parsed['sheets'].items():
        for cell in sheet['cells']:
            if cell['data_type'] == 'e' or cell.get('cached_type') == 'e':
                add('EXCEL_ERROR', 'error', name, cell['cell'], '单元格或保存缓存含 Excel 错误值。', '不能作为有效计算依据', 'C1-002')
            elif cell['data_type'] == 'f' and cell.get('cached_value') is None:
                add('CACHE_MISSING', 'needs_decision', name, cell['cell'], '公式没有保存结果；本工具不执行公式。', '需要原始值或重算后的资料', 'C1-002')

    def index_table(name, key):
        rows = table_rows(parsed, name)
        if rows is None:
            skipped.append(dict(check='reference_index', sheet=name, reason='缺表或表头结构不可靠'))
            return None
        index = {}
        for fields in rows:
            cell = fields[key]
            value = cell['value']
            try:
                check_value(value, 'id', key)
                if cell['data_type'] == 'f':
                    raise ContractError('编号不能是公式')
            except ContractError:
                add('INVALID_ID', 'error', name, cell['cell'], '编号为空、格式非法或由公式生成。', '无法建立可靠引用')
                continue
            if value in index:
                add('DUPLICATE_ID', 'error', name, cell['cell'], '编号重复：'+value, '不得采用最后一行覆盖', record_id=value)
                index[value] = None
            else:
                index[value] = fields
        return index

    accounts = index_table('账户资料', '账户编号')
    payees = index_table('收款人资料', '收款人编号')
    if accounts is not None:
        for key, fields in accounts.items():
            if fields is None:
                continue
            if empty(fields['归属用户']['value']):
                add('OWNER_MISSING', 'error', '账户资料', fields['归属用户']['cell'], '缺少归属用户。', '账户身份无法核验', record_id=key)
            if fields['账户类型']['value'] == '支付渠道':
                binding = fields['绑定关系']
                targets = re.findall(r'\b(?:ACC|CH)-[A-Za-z0-9_-]+\b', str(binding['value']))
                if len(targets) != 1:
                    add('BINDING_UNRESOLVED', 'needs_decision', '账户资料', binding['cell'], '无法确定唯一渠道绑定对象。', '不自动归并账户', record_id=key)
                elif targets[0] not in accounts or accounts[targets[0]] is None:
                    add('BINDING_TARGET_MISSING', 'error', '账户资料', binding['cell'], '绑定目标不存在或编号重复。', '渠道引用无效', record_id=key)
                elif targets[0] == key or accounts[targets[0]]['账户类型']['value'] == '支付渠道':
                    add('CHANNEL_CHAIN', 'error', '账户资料', binding['cell'], '渠道绑定自身或另一渠道；当前字典要求直接绑定账户。', '拒绝循环或多级推断', record_id=key)
    add('MAPPING_NOT_APPROVED', 'needs_decision', '账户资料', '', '结构检查不批准账户、用户、渠道与 demo 编号映射。', '正式转换仍需 C1-001 的明确决议')

    declarations = table_rows(parsed, '分类规则')
    allowed = {}
    if declarations is None:
        skipped.append(dict(check='category_membership', sheet='分类规则', reason='分类字典不可用'))
    else:
        for row in declarations:
            kind = row['交易类型']['value']
            if kind in KINDS:
                cell = row['适用消费分类']
                if cell['data_type'] == 'f':
                    continue
                allowed.setdefault(kind, set()).update(str(cell['value']).split('/'))

    ledger_ok = '小账本' in parsed['sheets'] and not any(i['sheet'] == '小账本' for i in parsed['issues'])
    if not ledger_ok:
        skipped.append(dict(check='ledger_fields_and_references', sheet='小账本', reason='缺表或表头结构不可靠'))
    elif payees is not None:
        ledger_ids = {r['fields']['流水号']['value'] for r in parsed['ledger_rows']}
        for pid, fields in payees.items():
            if fields is None:
                continue
            link = fields['关联流水']
            for tid in re.findall(r'\bLX-[A-Za-z0-9_-]+\b', str(link['value'])):
                if tid not in ledger_ids:
                    add('PAYEE_LEDGER_REFERENCE', 'error', '收款人资料', link['cell'],
                        '关联流水不存在：'+tid, '收款人关联无法核对', record_id=pid)
    seen = set()
    payee_names = Counter(str(row['收款人姓名']['value']) for row in (payees or {}).values() if row is not None)
    for row in parsed['ledger_rows'] if ledger_ok else []:
        fields = row['fields']
        values = {name: cell['value'] for name, cell in fields.items()}
        tid = values['流水号']
        record_id = tid if isinstance(tid, str) else None
        def flag(code, level, field, message, impact='阻止该条作为有效标准流水', blocker='C1-001'):
            add(code, level, '小账本', fields[field]['cell'], message, impact, blocker, record_id)
        for field in ('流水号', '日期', '摘要', '交易类型', '方向', '金额（元）', '账户', '账户编号'):
            if empty(values[field]):
                flag('REQUIRED', 'error', field, '必填值缺失。')
            if fields[field]['data_type'] == 'f':
                flag('SOURCE_FORMULA', 'needs_decision', field, '关键原始字段为公式，不能把缓存当作确认的原值。')
        for field in ('消费分类', '收款人/关联方', '原交易关联编号'):
            if fields[field]['data_type'] == 'f':
                flag('SOURCE_FORMULA', 'needs_decision', field, '分类或关联字段为公式，需确认原始依据。')
        try:
            check_value(tid, 'id', '流水号')
            if tid in seen:
                flag('DUPLICATE_ID', 'error', '流水号', '流水号重复。')
            seen.add(tid)
        except ContractError:
            flag('INVALID_ID', 'error', '流水号', '流水号不符合共享编号格式。')
        kind, direction = values['交易类型'], values['方向']
        if kind not in KINDS:
            flag('UNKNOWN_KIND', 'error', '交易类型', '未知交易类型，不自动分类。')
        if direction not in ('收入', '支出'):
            flag('INVALID_DIRECTION', 'error', '方向', '方向只能为收入或支出。')
        elif (kind in ('消费', '订阅') and direction != '支出') or (kind in ('收入', '退款') and direction != '收入'):
            flag('KIND_DIRECTION', 'error', '方向', '方向与该类型的源字典定义不一致。')
        try:
            value = values['金额（元）']
            if type(value) not in (int, float, str) or len(str(value)) > 80:
                raise ValueError()
            amount = Decimal(str(value))
            if not amount.is_finite() or amount == 0 or abs(amount) > Decimal(MAX_SAFE_INTEGER) / 100:
                raise ValueError()
            minor = amount * 100
            if minor != minor.to_integral_value():
                raise ValueError()
            if direction in ('收入', '支出') and (amount > 0) != (direction == '收入'):
                flag('AMOUNT_DIRECTION', 'error', '金额（元）', '金额符号与方向不一致。')
        except (InvalidOperation, ValueError, OverflowError):
            flag('INVALID_AMOUNT', 'error', '金额（元）', '金额必须非零、有限、精确到分且在安全范围内，不自动四舍五入。')
        try:
            raw_date = values['日期']
            if not isinstance(raw_date, str):
                raise ValueError()
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date):
                day = date.fromisoformat(raw_date)
            elif fields['日期']['data_type'] == 'd':
                stamp = datetime.fromisoformat(raw_date)
                day = stamp.date()
                if stamp.time().isoformat() != '00:00:00':
                    flag('TIME_UNCONFIRMED', 'needs_decision', '日期', 'Excel 含时分秒但没有明确时区，不推断实际发生时间。')
            else:
                raise ValueError()
            month_cell = fields['月份']
            month = month_cell.get('cached_value') if month_cell['data_type'] == 'f' else values['月份']
            if month is not None and month != day.strftime('%Y-%m'):
                flag('MONTH_MISMATCH', 'error', '月份', '月份或其缓存与原始日期不一致。', blocker='C1-002')
            elif empty(month) and month_cell['data_type'] != 'f':
                flag('REQUIRED', 'error', '月份', '月份缺失。')
        except (ValueError, TypeError):
            flag('INVALID_DATE', 'error', '日期', '日期非法或无法按已声明的日期精度解释。')
        if kind in ('消费', '订阅'):
            if empty(values['消费分类']):
                flag('CATEGORY_MISSING', 'error', '消费分类', '消费/订阅必须有独立分类。')
            elif kind in allowed and values['消费分类'] not in allowed[kind]:
                flag('CATEGORY_UNKNOWN', 'error', '消费分类', '分类不在源字典的对应类型中；不从商户猜测。')
            elif kind not in allowed:
                flag('CATEGORY_RULE_MISSING', 'needs_decision', '消费分类', '没有可用的对应分类字典。')
        aid = values['账户编号']
        if accounts is None:
            pass  # coverage 显式记录未执行引用检查，避免把整列误报为不存在。
        elif aid not in accounts or accounts[aid] is None:
            flag('ACCOUNT_REFERENCE', 'error', '账户编号', '账户/渠道编号不存在或不唯一。')
        if kind in ('转账', '退款'):
            party = values['收款人/关联方']
            if empty(party):
                flag('PARTY_MISSING', 'error', '收款人/关联方', '源字典要求此类型填写关联方；若未知需明确缺失处理。')
            elif kind == '转账' and payees is not None and payee_names[str(party)] != 1:
                flag('PAYEE_UNRESOLVED', 'needs_decision', '收款人/关联方', '姓名不能唯一匹配收款人；不得选第一个。')
        if kind in ('退款', '转账', '理财'):
            if empty(values['原交易关联编号']) or values['原交易关联编号'] == '外部待补':
                flag('ORIGINAL_PENDING', 'needs_decision', '原交易关联编号', '原关联资料未齐。', '阻止原交易核验，不阻止本期退款单列')
            else:
                flag('PAIRING_PENDING', 'needs_decision', '原交易关联编号', '保留源关联编号，不按 TR/INV 编号形似自动配对。', '需明确配对依据')
    add('DATE_PRECISION_PENDING', 'needs_decision', '字段说明', '', '只保留来源日期；午夜是否可作程序归属占位仍需确认。', '本步不产生 occurred_at')
    if parsed['source_sha256'] == REVIEWED_SHA:
        for cell, tid in (('D5', 'LX-003'), ('D13', 'LX-011')):
            add('KNOWN_PARTY_CONFLICT', 'needs_decision', '小账本', cell, '此已复核版本的摘要写小李，关联方写小王甲。', '按 C1-001 确认对象，不自动纠正', record_id=tid)
    else:
        add('VERSION_REVIEW_REQUIRED', 'needs_decision', '', '', '输入不是已复核的 V1.1 指纹，不套用旧行号语义结论。', '版本特定人工问题需重新复核')
    return dict(source_sha256=parsed['source_sha256'], issues=issues, skipped_checks=skipped,
                checked_ledger_rows=len(parsed['ledger_rows']) if ledger_ok else 0,
                counts=dict(Counter(i['level'] for i in issues)), import_ready=False,
                scope='V2 fields/references only; no business approval, totals, export or import')
