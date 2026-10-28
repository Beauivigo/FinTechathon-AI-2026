from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform, LABELS
from app.money import amount_input, parse_yuan, yuan_text
from bank.resource_labels import card_name
from app.presentation import (CARD_ACTION_LABELS, CARD_STATE_LABELS,
                              card_available_actions, card_unavailable_reasons)

st.set_page_config(page_title='卡片管理', layout='wide')
from app import ui
from app import visual

# 先注入固定静态样式，再渲染页面内容；st.set_page_config 已在本文件最前面调用。
visual.apply('core')
from app.auth_ui import gate
gate()
ui.render_sidebar_nav('cards')
with visual.band('core', 'cards'):
    ui.render_page_header('卡片管理')
    ui.render_app_nav('cards')

with visual.card('core', 'main'):
    st.caption('按当前卡状态只提供可办理的动作；卡片状态操作仍需身份验证。申请受理不代表发卡。')
    with st.expander('卡片资料'):
        st.caption('E V1.0 卡资料；已有旧工程卡与历史操作保留。')
    flow = current()
    cards = flow.resources['cards']
    ui.render_records([{'卡片': card_name(c['card_id']),
                   '状态': CARD_STATE_LABELS.get(c['card_state'], c['card_state']),
                   '单笔限额（元）': yuan_text(c['limit_minor'])} for c in cards], hide_index=True,
                   title_field='卡片', value_field='单笔限额（元）', meta_fields=('状态',),
                   key='card_summary_records', layout='bankcards', tone='core', limit=None)
    with st.expander('卡片编号与资料'):
        st.dataframe([{'卡片编号': c['card_id'], '账户': c['account_id'],
                       '状态': CARD_STATE_LABELS.get(c['card_state'], c['card_state'])} for c in cards], hide_index=True)
    if flow.blocked:
        from app import pending_view
        st.warning('已有待处理或待核实的操作，卡片操作暂不可提交；请先在此确认、取消或查询原操作。')
        pending_view.render_unresolved(flow, key_prefix='card_pending', include_local=False,
            note='冻结/解冻/挂失仍需原身份验证；处理完待办后本页提交按钮自动恢复。')
    if cards:
        selected = st.selectbox('选择卡片', cards, key='card_select',
            format_func=lambda c: f"{card_name(c['card_id'])} · "
                                  f"{CARD_STATE_LABELS.get(c['card_state'], c['card_state'])}")
        state = selected['card_state']
        available = card_available_actions(state)
        action = st.selectbox('卡片操作', available, key='card_action',
            format_func=lambda a: CARD_ACTION_LABELS.get(a, LABELS.get(a, a)))
        reasons = card_unavailable_reasons(state)
        if reasons:
            with st.expander('当前状态不可办理的动作'):
                for reason in reasons:
                    st.caption(reason)
                st.caption('状态由账本回执更新；不可办理时不会向银行提交。')
        params = dict(card_id=selected['card_id'])
        if action == 'card.set_limit':
            st.write(f"当前单笔限额：{yuan_text(selected['limit_minor'])}")
            limit_yuan = amount_input('新单笔限额（元，0 表示不限额）', 'card_limit_'+selected['card_id'], allow_zero=True)
            st.caption('设置只保存限额，不改变卡片状态、不扣款；商户下单执行时会复验单笔限额。普通账户转账仍按自身规则处理。')
            if st.button('核对并设置限额', key='card_submit', disabled=flow.blocked):
                try:
                    params['limit_minor'] = parse_yuan(limit_yuan, allow_zero=True)
                except ValueError as exc:
                    st.warning(str(exc))
                else:
                    perform(flow.start_action, action, params)
        else:
            target = CARD_ACTION_LABELS.get(action, action)
            st.write(f"{CARD_STATE_LABELS.get(state, state)} → {target}")
            st.caption('核对卡片和影响后提交；解挂后仍需另行解冻，申请受理不代表发卡。')
            if st.button(f"核对并{target}", key='card_submit', disabled=flow.blocked):
                perform(flow.start_action, action, params)
    st.subheader('申请受理')
    accounts=flow.resources['accounts']
    if accounts:
        account=st.selectbox('申请账户',accounts,key='card_apply_account',
            index=0 if len(accounts)==1 else None,format_func=lambda x:x['display_name'])
        st.caption('已有正常卡或待审核申请时拒绝重复申请；申请受理不代表发卡。')
        if st.button('申请卡片',key='card_apply',disabled=flow.blocked or account is None):
            perform(flow.start_action,'card.apply',dict(account_id=account['account_id']))
    render(flow, action_prefix='card.')
