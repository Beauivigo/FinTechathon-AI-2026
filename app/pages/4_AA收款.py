from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform, demo_payees
from shared import adapt_request, validate_result, ContractError, ServiceError
from bank.transfer import split_shares

st.set_page_config(page_title='AA 模拟收款单', layout='wide')
from app import ui

ui.render_sidebar_nav('aa')
ui.render_page_header('AA 收款')
ui.render_app_nav('aa')

with st.container(border=True):
    st.info('创建本地模拟收款单，不发送通知、不执行付款，也不会改变任何账户余额。')
    flow = current()
    try:
        people = demo_payees(flow)
    except (ContractError, ServiceError):
        st.error('模拟联系人暂不可用。')
        render(flow)
        st.stop()
    accounts = flow.resources['accounts']
    account = st.selectbox('收款账户', accounts, format_func=lambda a: a['display_name'], key='split_account')
    names = {p['payee_id']: f"{p['display_name']} · {p['masked_account']}" for p in people}
    selected = st.multiselect('依次选择参与人', list(names), default=list(names), format_func=names.get, key='split_people')
    amount = st.text_input('总金额（元，最多两位小数）', value='10.01', key='split_amount')
    st.caption('演示联系人来自李明及两位小王的查询结果。按选择顺序，前面的参与人优先分配余下的分。')
    valid = False
    try:
        preview = adapt_request('transfer.split', dict(account_id=account['account_id'], payee_ids=selected,
            amount_yuan=amount), request_id='preview', trace_id='preview', idempotency_key='preview')
        st.dataframe([{'顺序': i + 1, '参与人': names[s['payee_id']], '金额（分）': s['amount_minor']}
            for i, s in enumerate(split_shares(preview))], hide_index=True)
        valid = True
    except ContractError:
        st.warning('请选择参与人并输入有效金额；每人至少分配 1 分。')
    if st.button('提交 AA 收款单', key='split_submit', disabled=flow.blocked or not valid):
        perform(flow.start_action, 'transfer.split', preview['params'])
    render(flow)
