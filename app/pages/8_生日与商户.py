from pathlib import Path
import sys
from datetime import timedelta
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform
from shared import adapt_request, ServiceError
from bank.merchant import SHANGHAI

st.set_page_config(page_title='模拟生日与商户',layout='wide')
st.title('生日计划与模拟采购')
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
if usable:
    reservation=st.selectbox('使用已有预留',usable,key='merchant_reservation',format_func=lambda x:f"{x['purpose']} · 剩余{x['remaining_reserved_minor']/100:.2f}元")
    item=st.selectbox('本次下单商品',items,key='merchant_item',format_func=lambda x:f"{x['name']} · {x['price_minor']/100:.2f}元")
    deliver=st.date_input('计划配送日',value=max(birthday,today+timedelta(days=1)),min_value=today+timedelta(days=1),key='merchant_deliver')
    cards=[c['card_id'] for c in flow.resources['cards'] if c['account_id']==account['account_id']]
    card=st.selectbox('支付卡', ['自动选择']+cards,key='merchant_card')
    if st.button('申请本笔下单',key='merchant_order',disabled=flow.blocked):
        params=dict(account_id=account['account_id'],reservation_id=reservation['reservation_id'],item_id=item['item_id'],amount_minor=item['price_minor'],deliver_on=deliver.isoformat(),delivery_address_id=address['delivery_address_id'])
        if card!='自动选择': params['card_id']=card
        perform(flow.start_action,'merchant.order',params)
    if st.button('申请释放可核实剩余预算',key='merchant_release',disabled=flow.blocked):
        perform(flow.start_action,'budget.release',dict(reservation_id=reservation['reservation_id']))
orders=flow.gateway.list_orders(principal_id=flow.principal_id)
if orders:
    st.subheader('已提交模拟订单')
    names={i['item_id']:i['name'] for i in items}
    st.dataframe([{'订单编号':o['order_id'],'商品':names[o['item_id']],'金额（元）':o['amount_minor']/100,'计划配送日':o['deliver_on'],'支付卡':o['card_id']} for o in orders],hide_index=True)
render(flow)
