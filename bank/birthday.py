"""Explicit birthday timing compiled into a persistent, authorization-gated plan."""
from datetime import date, datetime, timedelta, time
import re
from uuid import uuid4
from agent.model_bank import BankPlanRunner, NeedsInput, money_minor
from .merchant import SHANGHAI
from .plan_store import PlanStore

SCHEDULE_FIELDS = ('birthday', 'budget_text', 'item_ids', 'delivery_address_id')


def schedule_ready(fields):
    """True only when a model draft carries every trusted birthday input."""
    return isinstance(fields, dict) and all(fields.get(name) for name in SCHEDULE_FIELDS)


def _slot_time(value, name):
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        raise NeedsInput('请给出带时区的' + name + '。')
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        raise NeedsInput('请给出带时区的' + name + '，例如2026-11-01T09:00:00+08:00。') from None
    if parsed.utcoffset() is None:
        raise NeedsInput(name + '必须带时区。')
    return parsed


def _item_ids(value):
    if isinstance(value, list):
        raw = [str(item).strip() for item in value]
    else:
        raw = [item.strip() for item in re.split(r'[,，、]', str(value or ''))]
    raw = [item for item in raw if item]
    if not raw or len(raw) != len(set(raw)):
        raise NeedsInput('请选择有效且不重复的商品编号。')
    return raw


def tasks_from_slots(flow, fields, *, previous=None):
    """Trusted page-equivalent compiler for a complete natural-language birthday draft.

    Missing, foreign or contradictory input raises instead of guessing; default
    times follow the existing create_plan rules (birthday month 1st 09:00 and
    two days before the birthday 09:00, Shanghai time).
    """
    if not schedule_ready(fields):
        raise NeedsInput('完整生日定时计划还需要生日日期、预算金额、商品编号和配送地址。')
    resources = flow.gateway.get_context(principal_id=flow.principal_id)
    try:
        birthday = date.fromisoformat(str(fields['birthday']).strip())
    except (TypeError, ValueError):
        raise NeedsInput('请给出有效的生日日期，例如2026-11-20。') from None
    budget_minor = money_minor(str(fields['budget_text']))
    item_ids = _item_ids(fields['item_ids'])
    known = {item['item_id'] for item in resources['items']}
    if any(item not in known for item in item_ids):
        raise NeedsInput('商品编号必须来自当前模拟商户。')
    address = fields['delivery_address_id']
    if address not in {row['delivery_address_id'] for row in resources['addresses']}:
        raise NeedsInput('配送地址不属于当前用户的模拟资源。')
    account = fields.get('account_id')
    accounts = [row['account_id'] for row in resources['accounts']]
    if not account and len(accounts) == 1:
        account = accounts[0]
    if account not in accounts:
        raise NeedsInput('请从当前用户账户中明确选择付款账户。')
    now = flow.gateway.clock().astimezone(SHANGHAI)
    budget_at = _slot_time(fields.get('budget_at'), '预算处理时间')
    purchase_at = _slot_time(fields.get('purchase_at'), '采购时间')
    if previous is not None:
        for slot, intent in (('budget_at', 'budget_reserve'), ('purchase_at', 'merchant_order')):
            if fields.get(slot):
                continue
            times = {t.get('not_before') for t in previous.tasks if t.get('intent') == intent}
            if len(times) != 1 or None in times:
                raise NeedsInput('原计划时间缺失或不一致，请明确提供预算与采购时间。')
            inherited = _slot_time(times.pop(), '原计划时间')
            if inherited is None:
                raise NeedsInput('原计划时间缺失，请明确提供预算与采购时间。')
            if slot == 'budget_at':
                budget_at = inherited
            else:
                purchase_at = inherited
    if budget_at is None:
        budget_at = datetime.combine(max(birthday.replace(day=1), now.date()), time(9, 0), SHANGHAI)
        if budget_at < now:
            raise NeedsInput('本月默认预算处理时刻已过；请说明带时区的预算处理时间，或到“生日与商户”页保存。')
    if purchase_at is None:
        purchase_at = datetime.combine(birthday - timedelta(days=2), time(9, 0), SHANGHAI)
    return build_tasks(flow, birthday=birthday, budget_minor=budget_minor, account_id=account,
        item_ids=item_ids, address_id=address, budget_at=budget_at, purchase_at=purchase_at)


def build_tasks(flow, *, birthday, budget_minor, account_id, item_ids, address_id, budget_at, purchase_at):
    """Existing birthday validation, returning the untrusted-but-shaped task list."""
    now = flow.gateway.clock().astimezone(SHANGHAI)
    if birthday < now.date() + timedelta(days=4):
        raise ValueError('生日至少为四天后；已错过的采购时间不能自动补办。')
    if budget_at.tzinfo is None or purchase_at.tzinfo is None:
        raise ValueError('计划时间必须带时区。')
    budget_at = budget_at.astimezone(SHANGHAI)
    purchase_at = purchase_at.astimezone(SHANGHAI)
    if (budget_at < now or budget_at.date().replace(day=1) != birthday.replace(day=1)
            or purchase_at.date() != birthday - timedelta(days=2) or purchase_at < budget_at):
        raise ValueError('预算须在生日当月、采购须提前两天且晚于预算处理；月初生日可能冲突，请人工核对日期。')
    resources = flow.gateway.get_context(principal_id=flow.principal_id)
    if account_id not in {r['account_id'] for r in resources['accounts']} or address_id not in {r['delivery_address_id'] for r in resources['addresses']}:
        raise ValueError('账户或地址不属于可信资源。')
    items = [i for i in resources['items'] if i['item_id'] in item_ids]
    if not items or len(items) != len(set(item_ids)) or len(item_ids) != len(set(item_ids)):
        raise ValueError('请选择有效且不重复的商品。')
    if type(budget_minor) is not int or budget_minor < sum(i['price_minor'] for i in items):
        raise ValueError('预算不足以覆盖所选商品。')
    tasks = [dict(id='reserve', title='生日预算预留', intent='budget_reserve', fields=dict(account_id=account_id,
                amount_text=f'{budget_minor // 100}.{budget_minor % 100:02d}', purpose='生日预算'), depends_on=[], not_before=budget_at.isoformat())]
    previous = 'reserve'
    for index, item in enumerate(items):
        key = 'order_' + str(index + 1)
        tasks.append(dict(id=key, title='采购 ' + item['name'], intent='merchant_order', fields=dict(account_id=account_id,
            reservation_id='@reserve.reservation_id', item_id=item['item_id'], amount_text=f"{item['price_minor'] // 100}.{item['price_minor'] % 100:02d}",
            deliver_on=birthday.isoformat(), delivery_address_id=address_id), depends_on=[previous], not_before=purchase_at.isoformat()))
        previous = key
    if budget_minor > sum(i['price_minor'] for i in items):
        # The release step keeps the same purchase-time gate as the orders so a
        # plan revision cannot leave an unfinished step without a trusted time
        # constraint; the dependency chain still requires both orders first.
        tasks.append(dict(id='release', title='确认释放剩余生日预算', intent='budget_release',
            fields={'reservation_id': '@reserve.reservation_id'}, depends_on=[previous],
            not_before=purchase_at.isoformat()))
    return tasks


def create_plan(flow, *, birthday, budget_minor, account_id, item_ids, address_id, budget_at, purchase_at):
    tasks = build_tasks(flow, birthday=birthday, budget_minor=budget_minor, account_id=account_id,
        item_ids=item_ids, address_id=address_id, budget_at=budget_at, purchase_at=purchase_at)
    runner = BankPlanRunner(flow, tasks, trace_id='plan_' + uuid4().hex, journal=PlanStore(flow.gateway.store))
    runner.automation = True
    runner.checkpoint('birthday_registered')
    return runner.trace_id
