"""表单复用现有 Conversation 授权/恢复，不另建网关或自动确认。"""
import streamlit as st
from agent.controller import Conversation
from app import demo
from shared import result_text, ServiceError, ContractError

LABELS = {'card.freeze': '冻结', 'card.unfreeze': '解冻', 'card.report_loss': '挂失',
          'card.release_loss': '解挂', 'card.set_limit': '设置限额', 'transfer.split': 'AA 收款单',
          'subscription.list': '查看订阅', 'subscription.review': '核实或排除订阅'}


def current():
    try:
        if 'action_flow' not in st.session_state:
            st.session_state.action_flow = Conversation(demo.gateway(), principal_id=demo.PRINCIPAL)
        flow = st.session_state.action_flow
        other_flow = st.session_state.get('conversation')
        flow.external_blocked = bool(other_flow and other_flow.needs_reconciliation)
        if flow.external_blocked:
            st.warning('转账结果尚待核实，请先到转账页面查询原操作。')
        flow.refresh_operations()
        flow.resources = flow.gateway.get_context(principal_id=flow.principal_id)
        return flow
    except Exception:
        st.error('模拟银行暂不可用，请检查原操作后再继续。')
        st.stop()


def perform(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except (ValueError, ServiceError, ContractError):
        st.warning('操作未完成，请查询原操作状态后再继续。')
        st.stop()
    st.rerun()


def render(flow):
    operations = [op for op in flow.operations if op['action'] in LABELS]
    with st.expander('恢复业务操作', expanded=not flow.request and bool(operations)):
        if operations:
            selected = st.selectbox('选择原操作', operations, key='action_history', format_func=lambda op:
                f"{LABELS[op['action']]} · {op['status']} · {op['idempotency_key']}")
            if st.button('恢复选中操作', key='action_recover'):
                perform(flow.recover, action=selected['action'], idempotency_key=selected['idempotency_key'])
        else:
            st.caption('暂无可恢复的业务操作。')
    if flow.blocked and not flow.request:
        st.warning('请先恢复待办。转账待办请到转账页面处理。')
    if not flow.request or not flow.result:
        return
    request, result = flow.request, flow.result
    st.subheader('当前操作：' + LABELS.get(request['action'], request['action']))
    p = request['params']
    if request['action'] == 'subscription.list':
        st.write('查询账户：' + p['account_id'])
        st.caption('确认后才读取本次模拟订阅列表。')
    if request['action'] == 'subscription.review':
        st.write('订阅：' + p['subscription_id'])
        st.write('本次选择：' + ('核实为订阅' if p['decision']=='accept' else '排除候选'))
        st.caption('核实不新签代扣授权；排除不取消既有扣费。')
    if 'card_id' in p:
        st.write('卡片：' + p['card_id'])
        st.caption('解挂后保持冻结，需要另行验证解冻。卡片状态尚未关联账户转账限制。')
        if request['action'] == 'card.set_limit':
            card = next((c for c in flow.resources['cards'] if c['card_id']==p['card_id']), None)
            if card:
                st.write(f"当前保存的限额：{card['limit_minor']} 分；本次设置值：{p['limit_minor']} 分")
            st.caption('限额保存不改变卡状态；支付限制联动待后续接入。')
    if 'payee_ids' in p:
        st.write('收款账户：' + p['account_id'])
        st.write('参与人顺序：' + ' → '.join(p['payee_ids']))
        st.write(f"总金额：{p['amount_minor'] // 100}.{p['amount_minor'] % 100:02d} 元")
        st.caption('只创建本地模拟收款单；不会发送通知，也不会执行参与人的付款。')
    st.write(result_text(request, result))
    if result['status'] == 'succeeded' and request['action'] == 'transfer.split':
        st.dataframe([{'参与人': s['payee_id'], '金额（分）': s['amount_minor']}
                      for s in result['data']['shares']], hide_index=True)
        st.caption('收款单：' + result['data']['collection_id'])
    if flow.pending:
        verify = result['status'] == 'awaiting_verification'
        if st.button('模拟验证并执行' if verify else '确认本次操作', key='action_approve'):
            perform(flow.decide, True, simulate_verification=verify)
        if st.button('拒绝此操作', key='action_reject'):
            perform(flow.decide, False)
    if st.button('查询原操作结果', key='action_query'):
        perform(flow.recover)
    st.caption('操作编号：' + request['idempotency_key'])
