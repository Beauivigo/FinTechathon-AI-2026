"""需求入口的 Streamlit 展示组件（仅草稿，不执行操作）。"""
from copy import deepcopy

import streamlit as st

from agent.routing import route

_DISCLAIMER = '这只是草稿，账户和业务状态仍需在表单核对；不会自动付款或执行取消。'
_INTENTS = {
    'bill': '账单', 'balance': '余额', 'transfer': '转账',
    'scheduled_transfer': '预约转账', 'card': '银行卡', 'aa': 'AA 收款',
    'subscription': '订阅管理', 'birthday': '生日计划与采购', 'products': '产品对比',
}
_FIELDS = {
    'account_id': '账户',
    'payee_query': '收款人',
    'amount_text': '金额原文',
    'period': '账单期间',
    'birthday': '生日日期',
    'execute_at': '预约时间',
    'card_id': '卡片编号',
    'subscription_id': '订阅编号',
    'card_operation': '卡片操作',
    'subscription_operation': '订阅操作',
    'product_ids': '产品编号',
    'participants': 'AA参与人',
}

def _label(name):
    return _FIELDS.get(str(name), '补充信息')

def _intent_label(name):
    return _INTENTS.get(name, '未识别')

def _show_fields(fields):
    if isinstance(fields, dict):
        for key, value in fields.items():
            if value not in (None, ''):
                st.text(f'{_label(key)}：{value}')

def render_router(*, blocked=False):
    st.subheader('需求入口')
    st.caption('离线规则识别，仅支持有限表达；结果只是草稿，不会自动执行。')
    if blocked:
        st.warning('请先处理或核实原操作')
    text = st.text_input('请描述你的需求', key='router_input', max_chars=200, disabled=blocked)
    identify_clicked = st.button('识别或补充需求', key='router_identify', disabled=blocked)
    clear_clicked = st.button('清除未执行草稿', key='router_clear', disabled=blocked)
    if identify_clicked and not blocked:
        st.session_state.pop('routing_handoff', None)
        st.session_state['router_result'] = route(text, st.session_state.get('router_result'))
    if clear_clicked and not blocked:
        st.session_state.pop('router_result', None)
        st.session_state.pop('routing_handoff', None)
    result = st.session_state.get('router_result')
    if isinstance(result, dict):
        status = result.get('status')
        message = result.get('message')
        if message:
            if status == 'ready':
                st.info(str(message))
            else:
                st.warning(str(message))
        intent = result.get('intent')
        if intent is not None:
            st.text(f'意图：{_intent_label(intent)}')
        _show_fields(result.get('fields'))
        missing = result.get('missing')
        if isinstance(missing, list) and missing:
            st.caption('还需补充：' + '、'.join(_label(item) for item in missing))
        st.caption(_DISCLAIMER)
        if status in ('ready', 'clarify') and intent in _INTENTS:
            if st.button('进入对应表单核对', key='router_open', disabled=blocked) and not blocked:
                return deepcopy(result)
    return None

def render_handoff(handoff):
    with st.expander('刚才的需求草稿'):
        st.caption('仅供核对，不会自动填充表单，也不会执行任何操作。')
        if not isinstance(handoff, dict):
            st.text('暂无可展示的草稿。')
            return None
        intent = handoff.get('intent')
        if intent is not None:
            st.text(f'意图：{_intent_label(intent)}')
        fields = handoff.get('fields')
        if isinstance(fields, dict) and fields:
            _show_fields(fields)
        else:
            st.text('暂无已填写字段。')
    return None
