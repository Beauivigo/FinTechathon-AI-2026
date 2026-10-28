from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform, demo_payees
from app.money import parse_yuan, yuan_text
from shared import adapt_request, validate_result, ContractError, ServiceError
from bank.transfer import split_shares

st.set_page_config(page_title='AA 收款', layout='wide')
from app import ui
from app import visual

# 先注入固定静态样式，再渲染页面内容；st.set_page_config 已在本文件最前面调用。
visual.apply('core')
from app.auth_ui import gate
gate()
ui.render_sidebar_nav('aa')
with visual.band('core', 'aa'):
    ui.render_page_header('AA 收款')
    ui.render_app_nav('aa')

with visual.card('core', 'main'):
    st.info('创建收款单，不发送通知、不执行付款，也不改变账户余额。')
    flow = current()
    try:
        people = demo_payees(flow)
    except (ContractError, ServiceError):
        st.error('联系人暂不可用。')
        render(flow, action_prefix='transfer.split')
        st.stop()
    accounts = flow.resources['accounts']
    account = st.selectbox('收款账户', accounts, index=0 if len(accounts)==1 else None,
        format_func=lambda a: a['display_name'], key='split_account')
    names = {p['payee_id']: f"{p['display_name']} · {p.get('bank_name','银行未提供')} · {p['masked_account']}" for p in people}
    selected = st.multiselect('依次选择参与人', list(names), default=[], format_func=names.get, key='split_people')
    amount = st.text_input('总金额（元，最多两位小数）', value='', key='split_amount')
    st.caption('联系人来自现有账户；按选择顺序，前面的参与人优先分配余下的分。')
    valid = False
    try:
        if account is None:
            raise ContractError('请选择收款账户')
        preview = adapt_request('transfer.split', dict(account_id=account['account_id'], payee_ids=selected,
            amount_yuan=amount), request_id='preview', trace_id='preview', idempotency_key='preview')
        ui.render_records([{'顺序': i + 1, '参与人': names[s['payee_id']], '金额（元）': yuan_text(s['amount_minor'])}
            for i, s in enumerate(split_shares(preview))], hide_index=True,
            title_field='参与人', value_field='金额（元）', meta_fields=('顺序',), limit=None,
            key='aa_share_preview_records', layout='tiles', tone='core')
        st.caption('核对每人金额后创建收款单；收款单只记录分摊，不代表已收款。')
        valid = True
    except (ContractError, ValueError):
        st.warning('请选择参与人并输入有效金额；每人至少分配 1 分，金额最多两位小数。')
    if st.button('核对并创建收款单', key='split_submit', disabled=flow.blocked or not valid):
        perform(flow.start_action, 'transfer.split', preview['params'])
    render(flow, action_prefix='transfer.split')
