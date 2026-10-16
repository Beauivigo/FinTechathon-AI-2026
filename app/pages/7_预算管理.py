from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform
from shared import ServiceError

st.set_page_config(page_title='模拟预算管理', layout='wide')
st.title('预算预留与释放')
st.info('模拟预算：预留不付款，释放不产生退款流水。商户消费与生日执行尚未接入。')
flow = current()
accounts = flow.resources['accounts']
if accounts:
    with st.form('budget_form'):
        account = st.selectbox('预留账户', accounts, key='budget_account', format_func=lambda a:a['display_name'])
        amount = st.number_input('预留金额（分）', min_value=1, max_value=9007199254740991, value=30000, step=1, key='budget_amount')
        purpose = st.text_input('用途', value='模拟生日预算', key='budget_purpose')
        if st.form_submit_button('申请预留预算', key='budget_reserve', disabled=flow.blocked or not purpose.strip()):
            perform(flow.start_action, 'budget.reserve', dict(account_id=account['account_id'], amount_minor=amount, purpose=purpose.strip()))
try:
    rows = flow.gateway.list_reservations(principal_id=flow.principal_id)
except ServiceError:
    st.error('预算列表暂不可用，请先核实原操作。')
    st.stop()
if rows:
    st.dataframe([{k:r[k] for k in ('reservation_id','account_id','purpose','amount_minor','remaining_reserved_minor','state')} for r in rows], hide_index=True)
    active = [r for r in rows if r['state']=='active']
    if active:
        selected = st.selectbox('选择要释放的预留', active, key='budget_release_selection',
                                format_func=lambda r:f"{r['purpose']} · {r['remaining_reserved_minor']} 分 · {r['reservation_id']}")
        if st.button('申请释放预算', key='budget_release', disabled=flow.blocked):
            perform(flow.start_action, 'budget.release', dict(reservation_id=selected['reservation_id']))
render(flow)
