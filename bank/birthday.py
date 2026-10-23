"""Explicit birthday timing compiled into a persistent, authorization-gated plan."""
from datetime import datetime,timedelta,time
from uuid import uuid4
from agent.model_bank import BankPlanRunner
from .merchant import SHANGHAI
from .plan_store import PlanStore


def create_plan(flow,*,birthday,budget_minor,account_id,item_ids,address_id,budget_at,purchase_at):
    now=flow.gateway.clock().astimezone(SHANGHAI)
    if birthday<now.date()+timedelta(days=4):raise ValueError('生日至少为四天后；已错过的采购时间不能自动补办。')
    if budget_at.tzinfo is None or purchase_at.tzinfo is None:raise ValueError('计划时间必须带时区。')
    budget_at=budget_at.astimezone(SHANGHAI);purchase_at=purchase_at.astimezone(SHANGHAI)
    if (budget_at<now or budget_at.date().replace(day=1)!=birthday.replace(day=1)
            or purchase_at.date()!=birthday-timedelta(days=2) or purchase_at<budget_at):
        raise ValueError('预算须在生日当月、采购须提前两天且晚于预算处理；月初生日可能冲突，请人工核对日期。')
    resources=flow.gateway.get_context(principal_id=flow.principal_id)
    if account_id not in {r['account_id'] for r in resources['accounts']} or address_id not in {r['delivery_address_id'] for r in resources['addresses']}:
        raise ValueError('账户或地址不属于可信资源。')
    items=[i for i in resources['items'] if i['item_id'] in item_ids]
    if not items or len(items)!=len(set(item_ids)) or len(item_ids)!=len(set(item_ids)):raise ValueError('请选择有效且不重复的商品。')
    if type(budget_minor) is not int or budget_minor<sum(i['price_minor'] for i in items):raise ValueError('预算不足以覆盖所选商品。')
    tasks=[dict(id='reserve',title='生日预算预留',intent='budget_reserve',fields=dict(account_id=account_id,
                amount_text=f'{budget_minor//100}.{budget_minor%100:02d}',purpose='生日预算'),depends_on=[],not_before=budget_at.isoformat())]
    previous='reserve'
    for index,item in enumerate(items):
        key='order_'+str(index+1)
        tasks.append(dict(id=key,title='采购 '+item['name'],intent='merchant_order',fields=dict(account_id=account_id,
            reservation_id='@reserve.reservation_id',item_id=item['item_id'],amount_text=f"{item['price_minor']//100}.{item['price_minor']%100:02d}",
            deliver_on=birthday.isoformat(),delivery_address_id=address_id),depends_on=[previous],not_before=purchase_at.isoformat()))
        previous=key
    if budget_minor>sum(i['price_minor'] for i in items):
        tasks.append(dict(id='release',title='确认释放剩余生日预算',intent='budget_release',fields={'reservation_id':'@reserve.reservation_id'},depends_on=[previous]))
    runner=BankPlanRunner(flow,tasks,trace_id='plan_'+uuid4().hex,journal=PlanStore(flow.gateway.store))
    runner.automation=True;runner.checkpoint('birthday_registered')
    return runner.trace_id
