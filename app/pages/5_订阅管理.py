from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform

st.set_page_config(page_title='模拟订阅管理',layout='wide')
st.title('订阅核实与排除')
st.info('E V1.0 固定模拟候选与已知订阅；尚未自动识别流水。核实不新签授权，排除不取消扣费。')
st.caption('仅使用日期依据完整的 SUB-E001 和已知 SUB-E003；取消扣费与续费提醒尚未接入。')
flow = current()
accounts = flow.resources['accounts']
if accounts:
    account = st.selectbox('订阅账户',accounts,key='subscription_account',format_func=lambda a:a['display_name'])
    if st.button('申请查看订阅',key='subscription_list',disabled=flow.blocked):
        perform(flow.start_action,'subscription.list',dict(account_id=account['account_id']))
if (flow.request and flow.request['action']=='subscription.list' and flow.result
        and flow.result['status']=='succeeded' and not flow.needs_reconciliation):
    rows = flow.result['data']['subscriptions']
    st.caption('本次已确认查询的快照；修改或需要最新状态时请重新申请查看。')
    st.dataframe(rows,hide_index=True)
    candidates = [s for s in rows if s['subscription_state']=='candidate']
    if candidates:
        selected = st.selectbox('选择候选',candidates,key='subscription_candidate',format_func=lambda s:s['merchant_name']+' · '+s['subscription_id'])
        st.write('候选依据：'+selected['evidence'])
        decision = st.selectbox('处理方式',['accept','reject'],key='subscription_decision',format_func=lambda d:'核实' if d=='accept' else '排除')
        if st.button('提交候选处理',key='subscription_review',disabled=flow.blocked):
            perform(flow.start_action,'subscription.review',dict(subscription_id=selected['subscription_id'],decision=decision))
render(flow)
