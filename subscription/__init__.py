"""E 订阅规则：固定候选、核实/排除，以及基于已入账流水的自动识别。

自动识别只创建 candidate，不扣费、不提醒、不自动核实；重复扫描按
“账户 + 商户”去重，已取消的商户不会复活。
"""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from shared import ContractError, ServiceError
from shared.contracts import RECORDS, fields
from shared.services import validate_subscription_change

SHANGHAI = timezone(timedelta(hours=8))
TOLERANCE_DAYS = 3
AMOUNT_TOLERANCE_PERCENT = 5
ELIGIBLE_KINDS = frozenset({"consumption", "subscription"})
MAX_CANDIDATES = 100


def record(row):
    result = {k:row[k] for k in RECORDS['subscription'][0]}
    fields(result, *RECORDS['subscription'])
    return result


def checked_review(c, request, principal):
    row = c.execute('''SELECT subscriptions.* FROM subscriptions JOIN accounts USING(account_id)
        WHERE subscription_id=? AND principal_id=?''',(request['params']['subscription_id'],principal)).fetchone()
    if row is None:
        raise ServiceError('FORBIDDEN','无权操作此订阅。')
    target = 'active' if request['params']['decision']=='accept' else 'dismissed'
    try:
        validate_subscription_change(request,row['subscription_state'],target)
        record(row)
    except ContractError as exc:
        raise ServiceError('INVALID_STATE','只能核实或排除候选订阅。') from exc
    return row, target


def _shanghai_date(value):
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return moment.astimezone(SHANGHAI).date()


def _add_months(anchor, months):
    """按锚日推进自然月/年；短月截断到当月最后一天，不跨月。"""
    total = anchor.month - 1 + months
    year, month = anchor.year + total // 12, total % 12 + 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def _max_deviation(items, step_months):
    """返回最大日期偏差；周期或金额不满足时返回 None。"""
    anchor = items[0][0]
    deviation = 0
    for index in range(1, len(items)):
        expected = _add_months(anchor, step_months * index)
        current = abs((items[index][0] - expected).days)
        if current > TOLERANCE_DAYS:
            return None
        deviation = max(deviation, current)
        previous, amount = items[index - 1][1], items[index][1]
        if abs(amount - previous) > previous * AMOUNT_TOLERANCE_PERCENT // 100:
            return None
    return deviation


def detect(items):
    """items 为按日期排序的 (日期, 金额分, 流水号)；返回 (interval, 最大偏差)。"""
    for interval, step in (('monthly', 1), ('yearly', 12)):
        deviation = _max_deviation(items, step)
        if deviation is not None:
            return interval, deviation
    return None, None


def _next_charge(anchor, step_months, last):
    index = 1
    while index <= 1200:
        candidate = _add_months(anchor, step_months * index)
        if candidate > last:
            return candidate
        index += 1
    raise ServiceError('INVALID_STATE', '订阅周期过长，无法推算下次扣费日。')


def _money(minor):
    return f"{minor // 100}.{minor % 100:02d}"


def scan(c, account_id):
    """识别账户流水中的周期扣费并写入 candidate；返回本次新建的行。"""
    rows = c.execute('''SELECT transaction_id, amount_minor, direction, kind, occurred_at, merchant_name
        FROM ledger_entries WHERE account_id=? ORDER BY occurred_at, transaction_id''', (account_id,)).fetchall()
    groups = {}
    for row in rows:
        merchant = (row['merchant_name'] or '').strip()
        if not merchant or row['direction'] != 'debit' or row['kind'] not in ELIGIBLE_KINDS:
            continue
        groups.setdefault(merchant, []).append(
            (_shanghai_date(row['occurred_at']), row['amount_minor'], row['transaction_id']))
    existing = {row[0] for row in c.execute(
        'SELECT merchant_name FROM subscriptions WHERE account_id=?', (account_id,))}
    created = []
    for merchant in sorted(groups):
        items = groups[merchant]
        if merchant in existing or len(items) < 2:
            continue
        interval, deviation = detect(items)
        if interval is None:
            continue
        step = 1 if interval == 'monthly' else 12
        first, last = items[0][0], items[-1][0]
        amounts = [item[1] for item in items]
        next_charge = _next_charge(first, step, last)
        evidence = (f"自动识别（E-03）：{len(items)} 期 {first.isoformat()}—{last.isoformat()}，"
                    f"金额 {_money(min(amounts))}—{_money(max(amounts))} 元，"
                    f"最大日期偏差 {deviation} 天，容差 ±{TOLERANCE_DAYS} 天/{AMOUNT_TOLERANCE_PERCENT}%")
        subscription_id = 'SUB-AUTO-' + uuid4().hex[:12]
        c.execute('''INSERT INTO subscriptions
            (subscription_id,account_id,merchant_name,amount_minor,interval,next_charge_date,
             subscription_state,evidence) VALUES (?,?,?,?,?,?,'candidate',?)''',
            (subscription_id, account_id, merchant, items[-1][1], interval,
             next_charge.isoformat(), evidence))
        created.append(c.execute('SELECT * FROM subscriptions WHERE subscription_id=?',
                                 (subscription_id,)).fetchone())
        if len(created) >= MAX_CANDIDATES:
            break
    return created
