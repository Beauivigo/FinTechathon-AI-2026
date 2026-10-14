"""V3 原表整体复核；与单账户 bill.analyze 和正式流水导出严格区分。"""
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re

from shared.contracts import ContractError, MAX_SAFE_INTEGER, check_value
from .checks import KINDS, check_material, empty, table_rows


def minor_value(value):
    """复核单元格中的带符号元值；允许汇总为零，不执行算式文本。"""
    if type(value) not in (int, float, str) or len(str(value)) > 80:
        raise ValueError('金额类型无效')
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) > Decimal(MAX_SAFE_INTEGER) / 100:
            raise ValueError('金额超范围')
        minor = amount * 100
        if minor != minor.to_integral_value():
            raise ValueError('金额不是整数分')
        return int(minor)
    except InvalidOperation as exc:
        raise ValueError('金额不可解析') from exc


def source_day(cell):
    value = cell['value']
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return date.fromisoformat(value)
    if cell['data_type'] == 'd' and isinstance(value, str):
        stamp = datetime.fromisoformat(value)
        if stamp.time().isoformat() == '00:00:00' and stamp.tzinfo is None:
            return stamp.date()  # 仅源记账日期，不生成实际交易时刻。
    raise ValueError('日期精度或时区未确认')


def reconcile_material(parsed):
    """每次从源流水独立复算。无效行时只给明确标识的小计，正式总额为 None。"""
    validation = check_material(parsed)
    preview, excluded, rows = [], [], []
    ids = Counter(r['fields']['流水号']['value'] for r in parsed['ledger_rows'])
    for source in parsed['ledger_rows']:
        fields = source['fields']
        values = {key: cell['value'] for key, cell in fields.items()}
        tid = values['流水号']
        candidate, problems = {}, []
        for name in ('流水号', '日期', '交易类型', '消费分类', '方向', '金额（元）'):
            if fields[name]['data_type'] in ('f', 'e'):
                problems.append(name+'不是可直接确认的原始值')
        try:
            check_value(tid, 'id', '流水号')
            if ids[tid] != 1:
                raise ValueError('重复流水号')
        except (ValueError, ContractError):
            problems.append('流水号无效或重复')
        try:
            signed = minor_value(values['金额（元）'])
            if signed == 0 or fields['金额（元）']['data_type'] in ('f', 'e'):
                raise ValueError()
            candidate['amount_minor'] = abs(signed)
        except ValueError:
            signed = None
            problems.append('金额无效')
        kind, direction = values['交易类型'], values['方向']
        if kind in KINDS:
            candidate['kind'] = KINDS[kind]
        else:
            problems.append('类型无效')
        if direction in ('收入', '支出'):
            candidate['direction'] = 'credit' if direction == '收入' else 'debit'
        else:
            problems.append('方向无效')
        if signed is not None and direction in ('收入', '支出') and (signed > 0) != (direction == '收入'):
            problems.append('金额与方向冲突')
        if (kind in ('消费', '订阅') and direction != '支出') or (kind in ('收入', '退款') and direction != '收入'):
            problems.append('类型与方向冲突')
        try:
            day = source_day(fields['日期'])
            candidate['source_date'] = day.isoformat()
        except (ValueError, TypeError):
            day = None
            problems.append('日期无法确定')
        category_issues = [i for i in validation['issues'] if i['sheet'] == '小账本'
                           and i['cell'] == fields['消费分类']['cell']]
        if kind in ('消费', '订阅'):
            if empty(values['消费分类']) or category_issues:
                problems.append('消费分类缺失或未通过字典检查')
            else:
                candidate['category'] = values['消费分类']
        item = dict(sheet='小账本', row=source['row'], source_transaction_id=tid,
                    source_account_or_channel=values['账户编号'],
                    source_original_reference=values['原交易关联编号'],
                    cells={key: cell['cell'] for key, cell in fields.items()}, candidate=candidate,
                    unresolved=['正式账户/用户映射', 'occurred_at 及真实时间精度', '原交易关联确认'],
                    arithmetic_eligible=not problems, reasons=problems)
        preview.append(item)
        if problems:
            excluded.append(dict(row=source['row'], source_transaction_id=tid, reasons=problems))
        else:
            rows.append(dict(id=tid, month=day.strftime('%Y-%m'), signed=signed,
                             kind=kind, category=values['消费分类']))

    months = {}
    for row in rows:
        bucket = months.setdefault(row['month'], dict(gross_minor=0, refund_minor=0, inflow_minor=0,
                  outflow_minor=0, count=0, categories={}, expense_ids=[], refund_ids=[]))
        bucket['count'] += 1
        amount = row['signed']
        bucket['inflow_minor' if amount > 0 else 'outflow_minor'] += abs(amount)
        if row['kind'] in ('消费', '订阅') and amount < 0:
            bucket['gross_minor'] += -amount
            cat = row['category']
            bucket['categories'][cat] = bucket['categories'].get(cat, 0) - amount
            bucket['expense_ids'].append(row['id'])
        elif row['kind'] == '退款' and amount > 0:
            bucket['refund_minor'] += amount
            bucket['refund_ids'].append(row['id'])
    metrics = ('gross_minor', 'refund_minor', 'inflow_minor', 'outflow_minor', 'count')
    totals = {key: sum(m[key] for m in months.values()) for key in metrics}
    overflow = any(value > MAX_SAFE_INTEGER for value in totals.values())
    structural = any(i['sheet']=='小账本' for i in parsed['issues']) or '小账本' not in parsed['sheets']
    complete = not excluded and not structural and not overflow
    comparisons, not_checked = [], []
    def compare(sheet, cell, expected, label):
        source = 'formula_cache' if cell['data_type'] == 'f' else 'literal'
        value = cell.get('cached_value') if source == 'formula_cache' else cell['value']
        observed = None
        status = 'unavailable'
        try:
            observed = minor_value(value)
            if complete and expected is not None:
                status = 'match' if observed == expected else ('sign_mismatch' if observed < 0 and -observed == expected else 'mismatch')
        except ValueError:
            pass
        comparisons.append(dict(sheet=sheet, cell=cell['cell'], label=label, source=source,
                                expected_minor=expected if complete else None, observed_minor=observed, status=status))

    stats = table_rows(parsed, '统计结果')
    if stats is None:
        not_checked.append('统计结果表结构不可用，未比较统计缓存')
    else:
        compared_months = []
        for fields in stats:
            period = fields['月份']['value']
            if not isinstance(period, str) or not (re.fullmatch(r'\d{4}-\d{2}', period) or period == '合计'):
                continue
            compared_months.append(period)
            expected = totals if period == '合计' else months.get(period, dict.fromkeys(metrics, 0))
            for name, metric in [('毛支出（消费+订阅）','gross_minor'), ('退款金额','refund_minor'),
                                 ('资金流出合计','outflow_minor'), ('总收入','inflow_minor')]:
                compare('统计结果', fields[name], expected[metric], period+' '+name)
        for month in sorted(months.keys() - set(compared_months)):
            not_checked.append('统计结果缺少月份：'+month)
        if '合计' not in compared_months:
            not_checked.append('统计结果缺少合计')

    hand = table_rows(parsed, '手算答案')
    if hand is None:
        not_checked.append('手算答案表结构不可用，未比较手算')
    else:
        covered = set()
        for fields in hand:
            period = fields['月份']['value']
            if period == '月份' and fields['交易类型']['value'] != '交易类型':
                break  # 已到另一个不同表头的手算区域，不复用上一区的列含义。
            if not isinstance(period, str):
                continue
            expected = None
            expected_ids = None
            if re.fullmatch(r'\d{4}-\d{2}', period):
                category = fields['消费分类']['value']
                expected = months.get(period, {}).get('categories', {}).get(category, 0)
                covered.add((period, category))
                expected_ids = [r['id'] for r in rows if r['month']==period and r['category']==category
                                and r['kind'] in ('消费', '订阅') and r['signed']<0]
            elif re.fullmatch(r'\d{4}-\d{2} 小计（毛支出）', period):
                expected = months.get(period[:7], {}).get('gross_minor', 0)
                expected_ids = months.get(period[:7], {}).get('expense_ids', [])
            elif period == '合计（毛支出）':
                expected = totals['gross_minor']
            else:
                continue  # 其他手算区域（净流量/流出）不是非负毛支出答案。
            compare('手算答案', fields['金额（元）'], expected, period+' '+str(fields['消费分类']['value']))
            if expected_ids is not None:
                cell = fields['纳入流水']
                text = str(cell['value'])
                # 本表明确使用 LX-002/006/007 等简写；只解析这一编号语法，不执行文字算式。
                found = ['LX-'+n for n in re.findall(r'(?:LX-|/)(\d+)', text)]
                status = 'unavailable'
                if complete and cell['data_type'] != 'f' and found:
                    status = 'match' if Counter(found)==Counter(expected_ids) else 'mismatch'
                comparisons.append(dict(sheet='手算答案', cell=cell['cell'], label=period+' 纳入流水',
                                        expected_ids=sorted(expected_ids) if complete else None,
                                        observed_ids=found, status=status))
        for month, bucket in months.items():
            for category in bucket['categories']:
                if (month, category) not in covered:
                    not_checked.append('手算缺少分类：'+month+' '+category)
    test_scopes = []
    cases = table_rows(parsed, '测试表')
    if cases is None:
        not_checked.append('测试表结构不可用，未核对查询范围')
    else:
        for fields in cases:
            case = fields['用例编号']['value']
            if not isinstance(case, str) or not case.startswith('TC-'):
                continue
            account = fields['账户']['value']
            if account == '全账户':
                reason = '全账户是资料整体复核范围，不是单账户 bill.analyze 请求。'
            elif isinstance(account, str) and account.startswith('CH-'):
                reason = '支付渠道不能直接作为正式 account_id；绑定关系尚待批准。'
            else:
                reason = '账户/日期及自然语言预期需确认后执行，不能把本次整体复算当作本用例通过。'
            test_scopes.append(dict(case_id=case, cell=fields['账户']['cell'], source_account=account,
                                    status='not_executed', reason=reason, blocker='C1-002'))
    return dict(source_sha256=parsed['source_sha256'], scope='原表整体复核，不是单账户 bill.analyze',
                arithmetic_complete=complete, material_ready=False, validation=validation,
                totals=totals if complete else None,
                valid_row_subtotals=months if not overflow else None,
                subtotal_label='原表全部有效行复算' if complete else '不完整小计，不可作为验收答案',
                overflow=overflow, excluded_rows=excluded, comparisons=comparisons,
                unperformed_comparisons=not_checked, test_scope_checks=test_scopes, preview=preview)
