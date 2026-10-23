from pathlib import Path
import sys
from datetime import timedelta
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform
from shared import adapt_request, ServiceError
from bank.merchant import SHANGHAI
from bank.merchant_scheduler import MerchantScheduler
from app.merchant_schedule_panel import merchant_schedule_panel

st.set_page_config(page_title='模拟生日与商户',layout='wide')
from app import ui

from app.auth_ui import gate
gate()
ui.render_sidebar_nav('merchant')
ui.render_page_header('生日计划与模拟采购')
ui.render_app_nav('merchant')

with st.container(border=True):
    st.info('商品和地址均为虚构。生成计划不付款；预留需确认，每笔下单需模拟强验证。配送日期是计划日期，不代表已经送达。')
    flow=current()
    today=flow.gateway.clock().astimezone(SHANGHAI).date()
    accounts=flow.resources['accounts'];items=flow.resources['items'];addresses=flow.resources['addresses']
    if not accounts or not items or not addresses:
        st.warning('当前没有完整的模拟采购资源。');st.stop()
    account=st.selectbox('付款账户',accounts,key='merchant_account',format_func=lambda x:x['display_name'])
    selected_items=st.multiselect('计划采购商品',items,default=[items[0],items[2]],key='birthday_items',format_func=lambda x:f"{x['name']} · {x['price_minor']/100:.2f}元")
    birthday=st.date_input('生日日期',value=today+timedelta(days=32),min_value=today,key='birthday_date')
    address=st.selectbox('模拟配送地址',addresses,key='merchant_address',format_func=lambda x:x['label'])
    with st.expander('保存完整生日计划并由后台准备到期待办'):
        from datetime import datetime,time
        from bank.birthday import create_plan
        from shared.adapters import yuan_to_minor
        local_now=flow.gateway.clock().astimezone(SHANGHAI)
        budget_text=st.text_input('生日总预算（元）',value='1000.00',key='birthday_budget_text')
        budget_day=st.date_input('预算处理日期（生日当月）',value=max(birthday.replace(day=1),today),key='birthday_budget_day')
        budget_time=st.time_input('预算处理时刻',value=(local_now+timedelta(minutes=5)).time().replace(second=0,microsecond=0),key='birthday_budget_time')
        purchase_time=st.time_input('提前两天的采购时刻',value=time(9,0),key='birthday_purchase_time')
        st.caption('到期自动准备待办；预算仍需确认，每笔订单仍需验证，剩余预算释放也需确认。请在AI银行助手恢复该计划办理。')
        if st.button('保存日期与商品并启用后台准备',key='birthday_save_workflow',disabled=flow.blocked or not selected_items):
            try:
                plan_id=create_plan(flow,birthday=birthday,budget_minor=yuan_to_minor(budget_text),account_id=account['account_id'],
                    item_ids=[i['item_id'] for i in selected_items],address_id=address['delivery_address_id'],
                    budget_at=datetime.combine(budget_day,budget_time,SHANGHAI),
                    purchase_at=datetime.combine(birthday-timedelta(days=2),purchase_time,SHANGHAI))
                st.success('完整计划已保存：'+plan_id+'；后台运行时将按期准备待办。')
            except (ValueError,ServiceError):st.warning('计划未保存，请核对预算、商品与时间；预算需在当月且不能晚于提前两天的采购时间。')
    if st.button('生成生日计划',key='birthday_plan',disabled=flow.blocked or not selected_items):
        req=adapt_request('scenario.plan',dict(goal='生日 '+birthday.isoformat()),request_id=uuid4().hex,trace_id=uuid4().hex)
        result=flow.gateway.execute(req,principal_id=flow.principal_id)
        if result['status']=='succeeded': st.session_state.birthday_plan_data=result['data']
        else: st.warning('请选择至少四天后的生日，以留出提醒和采购时间。')
    if st.session_state.get('birthday_plan_data'):
        st.write(st.session_state.birthday_plan_data['summary'])
        for step in st.session_state.birthday_plan_data['steps']: st.write(step['description'])
        if st.button('清除未执行计划',key='birthday_clear',disabled=flow.blocked):
            st.session_state.pop('birthday_plan_data',None);st.rerun()
    st.caption('计划是只读建议，不会后台采购。修改商品或日期后请重新核对；已预留预算需另行确认释放，已下单不会随清除计划撤销。')
    total=sum(i['price_minor'] for i in selected_items)
    st.write(f'所选商品合计：{total/100:.2f}元')
    if st.button('为所选商品申请预留',key='birthday_reserve',disabled=flow.blocked or not total):
        perform(flow.start_action,'budget.reserve',dict(account_id=account['account_id'],amount_minor=total,purpose='模拟生日采购 '+birthday.isoformat()))
    rows=flow.gateway.list_reservations(principal_id=flow.principal_id)
    usable=[r for r in rows if r['account_id']==account['account_id'] and r['state'] in ('active','partially_consumed') and r['remaining_reserved_minor']>0]
    draft_params=None
    if usable:
        reservation=st.selectbox('使用已有预留',usable,key='merchant_reservation',format_func=lambda x:f"{x['purpose']} · 剩余{x['remaining_reserved_minor']/100:.2f}元")
        item=st.selectbox('本次下单商品',items,key='merchant_item',format_func=lambda x:f"{x['name']} · {x['price_minor']/100:.2f}元")
        deliver=st.date_input('计划配送日',value=max(birthday,today+timedelta(days=1)),min_value=today+timedelta(days=1),key='merchant_deliver')
        cards=[c['card_id'] for c in flow.resources['cards'] if c['account_id']==account['account_id']]
        card=st.selectbox('支付卡', ['自动选择']+cards,key='merchant_card')
        draft_params=dict(account_id=account['account_id'],reservation_id=reservation['reservation_id'],item_id=item['item_id'],amount_minor=item['price_minor'],deliver_on=deliver.isoformat(),delivery_address_id=address['delivery_address_id'])
        if card!='自动选择': draft_params['card_id']=card
        if st.button('申请本笔下单',key='merchant_order',disabled=flow.blocked):
            perform(flow.start_action,'merchant.order',draft_params)
        if st.button('申请释放可核实剩余预算',key='merchant_release',disabled=flow.blocked):
            perform(flow.start_action,'budget.release',dict(reservation_id=reservation['reservation_id']))

    scheduler=MerchantScheduler(flow.gateway)
    tasks=scheduler.list_tasks(principal_id=flow.principal_id)
    for task in tasks:
        query=adapt_request('operation.get',dict(original_action='merchant.order',
            original_idempotency_key=task['spec']['payload']['idempotency_key']),request_id=uuid4().hex,trace_id=task['spec']['trace_id'])
        reply=flow.gateway.execute(query,principal_id=flow.principal_id)
        task['operation_status']=(reply['data']['operation_result']['status'] if reply['status']=='succeeded'
            else '尚未受理' if task['state'] in ('scheduled','cancelled') and reply.get('error',{}).get('code')=='NOT_FOUND' else '待核实')
    # 仅是输入初始值，非最短提前或迟到业务规则。
    default_run=flow.gateway.clock().astimezone(SHANGHAI)+timedelta(minutes=5)
    st.session_state.setdefault('msp_run_date',default_run.date())
    st.session_state.setdefault('msp_run_time',default_run.time().replace(second=0,microsecond=0,tzinfo=None))
    events=merchant_schedule_panel(draft_params,tasks,flow.gateway.clock(),blocked=flow.blocked)
    for event in events:
        if event['type']=='merchant_schedule_create':
            if flow.blocked or event['params']!=draft_params:
                st.warning('草稿或待办状态已变化，请重新核对。')
                continue
            perform(scheduler.register,event['params'],event['run_at'],event['idempotency_key'],principal_id=flow.principal_id,confirmed=True)
        elif event['type']=='merchant_schedule_check':
            perform(scheduler.run_once,principal_id=flow.principal_id)
        elif event['type']=='merchant_schedule_cancel':
            def cancel_selected():
                task=scheduler.cancel(event['task_id'],principal_id=flow.principal_id,confirmed=True)
                request=task['spec']['payload']
                if flow.request and flow.request['idempotency_key']==request['idempotency_key']:
                    flow.recover(action='merchant.order',idempotency_key=request['idempotency_key'])
            perform(cancel_selected)
        elif event['type']=='merchant_schedule_restore':
            task=next((t for t in tasks if t['task_id']==event['task_id']),None)
            if task and task['state'] in ('running','unknown','dispatched'):
                perform(flow.recover,action='merchant.order',idempotency_key=task['spec']['payload']['idempotency_key'])
            else:
                st.info('任务尚未受理或已在派发前取消，没有付款操作需要恢复。')
    orders=flow.gateway.list_orders(principal_id=flow.principal_id)
    if orders:
        st.subheader('已提交模拟订单')
        names={i['item_id']:i['name'] for i in items}
        st.dataframe([{'订单编号':o['order_id'],'商品':names[o['item_id']],'金额（元）':o['amount_minor']/100,'计划配送日':o['deliver_on'],'支付卡':o['card_id']} for o in orders],hide_index=True)
    render(flow)
