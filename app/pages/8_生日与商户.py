from pathlib import Path
import sys
from datetime import datetime, time, timedelta
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform
from app.money import amount_input, parse_yuan, yuan_text
from app.presentation import beijing
from app.saved_plan_view import render_saved_plan
from shared import adapt_request, ServiceError, ContractError
from bank.merchant import SHANGHAI
from bank.merchant_scheduler import MerchantScheduler
from app.merchant_schedule_panel import merchant_schedule_panel
from bank.birthday import build_tasks, create_plan
from bank.background import BackgroundController
from app.background_panel import render_background
from app import ui, visual

st.set_page_config(page_title='生日计划',layout='wide')
# 先注入固定静态样式，再渲染页面内容；st.set_page_config 已在本文件最前面调用。
visual.apply('core')
from app.auth_ui import gate
gate()
ui.render_sidebar_nav('merchant')
with visual.band('core', 'merchant'):
    ui.render_page_header('生日计划', '填写安排 → 核对计划 → 保存 → 查看进度和待办。')
    ui.render_app_nav('merchant')
flow=current()
today=flow.gateway.clock().astimezone(SHANGHAI).date()
accounts=flow.resources['accounts'];items=flow.resources['items'];addresses=flow.resources['addresses']
if not accounts or not items or not addresses:
    st.warning('当前没有完整的采购资源。');st.stop()
st.caption('保存计划不付款；预算预留需确认，每笔下单需身份验证。配送日期是计划日期，不代表已经送达。')
with st.form('birthday_details'):
    account=st.selectbox('付款账户',accounts,key='merchant_account',index=0 if len(accounts)==1 else None,format_func=lambda x:x['display_name'])
    birthday=st.date_input('生日日期',value=None,min_value=today+timedelta(days=4),key='birthday_date')
    selected_items=st.multiselect('计划采购商品',items,default=[],key='birthday_items',format_func=lambda x:x['name']+' · '+yuan_text(x['price_minor']))
    address=st.selectbox('配送地址',addresses,index=None,placeholder='请选择',key='merchant_address',format_func=lambda x:x['label'])
    budget_text=amount_input('生日总预算（元）','birthday_budget_text')
    budget_day=st.date_input('预算处理日期（生日当月）',value=None,key='birthday_budget_day')
    budget_time=st.time_input('预算处理时刻（北京时间）',value=time(9),key='birthday_budget_time')
    purchase_time=st.time_input('提前两天的采购时刻（北京时间）',value=time(9),key='birthday_purchase_time')
    if st.form_submit_button('核对生日计划',key='birthday_review',disabled=flow.blocked):
        st.session_state.pop('birthday_reviewed',None)
        try:
            if account is None or birthday is None or address is None or budget_day is None:
                raise ValueError('请填写日期并选择账户、商品和地址。')
            params=dict(birthday=birthday,budget_minor=parse_yuan(budget_text),account_id=account['account_id'],
                item_ids=[i['item_id'] for i in selected_items],address_id=address['delivery_address_id'],
                budget_at=datetime.combine(budget_day,budget_time,SHANGHAI),
                purchase_at=datetime.combine(birthday-timedelta(days=2),purchase_time,SHANGHAI))
            build_tasks(flow,**params)
            st.session_state['birthday_reviewed']=params
        except (ValueError,ServiceError,ContractError) as exc:
            st.warning(str(exc) if isinstance(exc,ValueError) else '计划未通过校验，请核对商品与时间。')
reviewed=st.session_state.get('birthday_reviewed')
if reviewed:
    with visual.card('core', 'review'):
        st.subheader('核对计划摘要')
        st.write('生日与配送日期：'+reviewed['birthday'].isoformat())
        st.write('商品：'+'、'.join(i['name'] for i in items if i['item_id'] in reviewed['item_ids']))
        st.write('地址：'+next(a['label'] for a in addresses if a['delivery_address_id']==reviewed['address_id']))
        st.write('总预算：'+yuan_text(reviewed['budget_minor']))
        st.write('预算处理：'+beijing(reviewed['budget_at'].isoformat()))
        st.write('采购处理：'+beijing(reviewed['purchase_at'].isoformat()))
        st.caption('上方修改后请再次点击“核对生日计划”，这里展示的是已核对的版本。保存只允许准备待办，不会启动后台或批准任何付款。')
        if st.button('保存这份计划',key='birthday_save_workflow',type='primary',disabled=flow.blocked):
            try:
                plan_id=create_plan(flow,**reviewed)
                st.session_state['birthday_open_plan']=plan_id
                st.session_state.pop('birthday_reviewed',None)
                st.session_state['birthday_saved_notice']='计划已保存。请在下方查看后台状态；未运行时不会自动准备待办。'
                st.rerun()
            except (ValueError,ServiceError,ContractError):
                st.warning('计划未保存，请重新核对日期、预算和商品。')
        if st.button('返回填写',key='birthday_edit'):
            st.session_state.pop('birthday_reviewed',None);st.rerun()
if st.session_state.get('birthday_saved_notice'):
    st.success(st.session_state.pop('birthday_saved_notice'))
render_background(flow)
render_saved_plan(flow)
render(flow, action_prefix=('budget.','merchant.','scenario.'))
with st.expander('手动管理采购与预算'):
    st.caption('下列工具独立于完整计划。请核对已有预留和订单，避免重复安排；不会自动撤销完整计划。')
    if birthday is None or address is None or account is None:
        st.caption('先选择生日、付款账户与地址，才可使用独立采购工具。')
    else:
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
        st.caption('计划为只读建议；后台只准备待办，不直接付款。已下单不会随清除计划撤销；已预留预算需另行确认释放。')
        total=sum(i['price_minor'] for i in selected_items)
        st.write('所选商品合计：'+yuan_text(total))
        if st.button('为所选商品申请预留',key='birthday_reserve',disabled=flow.blocked or not total):
            perform(flow.start_action,'budget.reserve',dict(account_id=account['account_id'],amount_minor=total,purpose='生日采购 '+birthday.isoformat()))
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
            st.subheader('已提交订单')
            names={i['item_id']:i['name'] for i in items}
            ui.render_records([{'订单编号':o['order_id'],'商品':names[o['item_id']],'金额（元）':o['amount_minor']/100,'计划配送日':o['deliver_on'],'支付卡':o['card_id']} for o in orders],hide_index=True,
                              title_field='商品', value_field='金额（元）', meta_fields=('计划配送日',),
                              key='merchant_order_records', layout='activity', tone='core', limit=None)
# 当前原操作与恢复入口已在主区渲染，不依赖上方手动表单是否填写。
