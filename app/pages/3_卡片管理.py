from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform, LABELS
from card import TARGETS

st.set_page_config(page_title='模拟卡片管理', layout='wide')
from app import ui

ui.render_sidebar_nav('cards')
ui.render_page_header('卡片管理')
ui.render_app_nav('cards')

with st.container(border=True):
    st.info('E V1.0 模拟卡资料；已有演示库保留旧工程卡与历史操作。四种状态操作均需要模拟强验证。')
    flow = current()
    cards = flow.resources['cards']
    names = {'CARD-E001': '模拟白金卡', 'CARD-E002': '模拟普通卡', 'CARD-E003': '模拟挂失卡'}
    st.dataframe([dict(c, 展示名称=names.get(c['card_id'], '旧工程模拟卡')) for c in cards], hide_index=True)
    if cards:
        selected = st.selectbox('选择卡片', cards, key='card_select',
            format_func=lambda c: f"{c['card_id']} · {c['card_state']}")
        action = st.selectbox('卡片操作', [*TARGETS, 'card.set_limit'], format_func=lambda a: LABELS[a], key='card_action')
        params = dict(card_id=selected['card_id'])
        if action == 'card.set_limit':
            st.write(f"当前限额：{selected['limit_minor']} 分")
            params['limit_minor'] = st.number_input('新限额（整数分，0—1000000）', min_value=0, max_value=1000000,
                value=selected['limit_minor'],step=1,key='card_limit_'+selected['card_id'])
            st.caption('任意卡状态均可设置，0允许保存。设置不改变状态、不扣款；商户下单执行时会复验单笔限额。')
        else:
            st.write(f"{selected['card_state']} → {TARGETS[action]}")
            st.caption('解挂后仍冻结；解冻需另行验证。')
        if st.button('提交卡片操作', key='card_submit', disabled=flow.blocked):
            perform(flow.start_action, action, params)
    st.subheader('申请受理')
    accounts=flow.resources['accounts']
    if accounts:
        account=st.selectbox('申请账户',accounts,key='card_apply_account',format_func=lambda x:x['display_name'])
        st.caption('已有正常卡或待审核申请时拒绝重复申请；申请受理不代表发卡。')
        if st.button('申请卡片',key='card_apply',disabled=flow.blocked):
            perform(flow.start_action,'card.apply',dict(account_id=account['account_id']))
    render(flow)
