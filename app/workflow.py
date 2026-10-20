"""表单复用现有 Conversation 授权/恢复，不另建网关或自动确认。"""
import streamlit as st
from uuid import uuid4
from agent.controller import Conversation
from app import demo
from shared import result_text, ServiceError, ContractError, adapt_request, validate_result
from app.routing_panel import render_handoff

LABELS = {'merchant.order':'模拟商户下单', 'card.apply':'卡片申请受理', 'subscription.cancel':'取消订阅', 'subscription.remind':'登记续费提醒', 'card.freeze': '冻结', 'card.unfreeze': '解冻', 'card.report_loss': '挂失',
          'card.release_loss': '解挂', 'card.set_limit': '设置限额', 'transfer.split': 'AA 收款单',
          'subscription.list': '查看订阅', 'subscription.review': '核实或排除订阅',
          'subscription.scan': '扫描识别订阅',
          'transfer.schedule': '登记定时转账', 'transfer.create': '转账执行',
          'budget.reserve': '预留预算', 'budget.release': '释放预算'}
LABELS.update({'wealth.assess':'风险评估','wealth.purchase':'理财申购',
               'wealth.redeem':'理财赎回','subscription.charge':'本期订阅续费'})


def demo_payees(flow):
    people = []
    for name in ('李明','小王'):
        request = adapt_request('payee.resolve',dict(query=name,match_type='name'),request_id=uuid4().hex,trace_id=uuid4().hex)
        result = validate_result(request,flow.gateway.execute(request,principal_id=flow.principal_id))
        if result['status']!='succeeded':
            raise ServiceError('UPSTREAM_ERROR','联系人查询未完成。')
        people.extend(result['data']['candidates'])
    return people


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
        if st.session_state.get('routing_handoff'):
            render_handoff(st.session_state.routing_handoff)
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
    if request['action']=='wealth.purchase':
        st.write('付款账户：'+p['account_id']+'；风险档案：'+p['risk_profile_id'])
        st.write('申购产品：'+p['product_id'])
        st.write(f"申购金额：{p['amount_minor']//100}.{p['amount_minor']%100:02d}元（含申购费）")
        product=next((item for item in flow.resources['products'] if item['product_id']==p['product_id']),None)
        if product:
            st.write(product['name']+'；'+product['risk_level']+'；'+product['fee_description'])
        st.caption('核对本笔申购内容后，需模拟强验证。')
    if request['action']=='wealth.redeem':
        st.write('到账账户：'+p['account_id']+'；持仓：'+p['holding_id'])
        st.write('本笔赎回份额：'+p['units']+'份')
        st.caption('模拟T+1：本演示在强验证成功后即时展示入账，以模拟次日到账，不代表真实即时到账。')
    if request['action']=='wealth.assess':
        st.write('风险问卷版本：'+p['questionnaire_version'])
    if request['action']=='subscription.charge':
        st.write('续费订阅：'+p['subscription_id'])
        try:
            details=flow.gateway.subscription_charge_details(request,principal_id=flow.principal_id)
        except (ServiceError,ContractError):
            details=None
        if details:
            st.write('扣费账户：'+details['account_id']+'；支付卡：'+details['card_id'])
            st.write(f"本期金额：{details['amount_minor']//100}.{details['amount_minor']%100:02d}元；对应扣费日：{details['charge_date']}")
        else:
            st.warning('无法核实原账户与支付卡绑定，请查询原操作并重新核对。')
        st.caption('本期续费需独立强验证；核实订阅不授予后续自动扣款权限。')
    if request['action']=='merchant.order':
        st.write(f"本笔金额：{p['amount_minor']/100:.2f}元；计划配送日：{p['deliver_on']}")
        st.write('商品：'+p['item_id']+'；模拟地址：'+p['delivery_address_id'])
        try:
            selected=flow.gateway.selected_order_card(request,principal_id=flow.principal_id)
        except (ServiceError,ContractError):
            selected=None
            st.warning('暂时无法核实支付卡，请先查询原操作。')
        if selected: st.write('本次绑定支付卡：'+selected)
        st.caption('强验证后才消费已有预留。未知结果先查询原操作，不换键重复支付。')
    if request['action']=='card.apply':
        st.caption('仅提交申请并取得受理编号，进入待审核；不会自动发卡。')
    if request['action'] in ('subscription.cancel','subscription.remind'):
        st.write('订阅：'+p['subscription_id'])
        if 'remind_at' in p: st.write('提醒时间：'+p['remind_at'])
        else: st.caption('取消后不再派发未发送的本地提醒，已发送历史保留；不撤销已完成支付。')
    if request['action'] == 'budget.reserve':
        st.write('预留账户：' + p['account_id'])
        st.write(f"预留金额：{p['amount_minor']} 分；用途：{p['purpose']}")
        st.caption('确认后减少可用余额，账面余额不变；没有下单或付款。')
    if request['action'] == 'budget.release':
        st.write('预留编号：' + p['reservation_id'])
        rows = flow.gateway.list_reservations(principal_id=flow.principal_id)
        reservation = next((r for r in rows if r['reservation_id']==p['reservation_id']), None)
        if reservation:
            st.write(f"账户：{reservation['account_id']}；用途：{reservation['purpose']}；当前剩余预留：{reservation['remaining_reserved_minor']} 分")
        if flow.pending:
            try:
                amount=flow.gateway.release_quote(request,principal_id=flow.principal_id)
                st.write(f'当前可释放金额：{amount/100:.2f}元；确认时仍会复验。')
            except (ServiceError,ContractError):
                st.warning('预留状态已变化，请查询原操作后重新核对。')
        st.caption('待核实订单占用金额继续保留；只释放可核实的剩余金额。')
    if request['action'] in ('transfer.schedule','transfer.create'):
        st.write('付款账户：'+p['account_id'])
        try:
            payee = flow.gateway.operation_payee(request,principal_id=flow.principal_id)
        except (ServiceError, ContractError):
            payee = None
        st.write('收款人：'+p['payee_id']+((' · '+payee['display_name']+' · '+payee['masked_account']) if payee else ''))
        st.write(f"金额：{p['amount_minor']//100}.{p['amount_minor']%100:02d} 元")
        if request['action']=='transfer.schedule':
            st.write('约定时间：'+p['execute_at'])
            st.caption('登记不扣款，到期另行确认；已派发不等于已付款。')
    if request['action'] == 'subscription.list':
        st.write('查询账户：' + p['account_id'])
        st.caption('确认后才读取本次模拟订阅列表。')
    if request['action'] == 'subscription.review':
        st.write('订阅：' + p['subscription_id'])
        st.write('本次选择：' + ('核实为订阅' if p['decision']=='accept' else '排除候选'))
        st.caption('核实不新签代扣授权；排除不取消既有扣费。')
    if 'card_id' in p and request['action'].startswith('card.'):
        st.write('卡片：' + p['card_id'])
        st.caption('解挂后保持冻结，需要另行验证解冻。卡片状态尚未关联账户转账限制。')
        if request['action'] == 'card.set_limit':
            card = next((c for c in flow.resources['cards'] if c['card_id']==p['card_id']), None)
            if card:
                st.write(f"当前保存的限额：{card['limit_minor']} 分；本次设置值：{p['limit_minor']} 分")
            st.caption('限额保存不改变卡状态；商户下单校验单笔限额；普通转账仍按自身规则处理。')
    if 'payee_ids' in p:
        st.write('收款账户：' + p['account_id'])
        st.write('参与人顺序：' + ' → '.join(p['payee_ids']))
        st.write(f"总金额：{p['amount_minor'] // 100}.{p['amount_minor'] % 100:02d} 元")
        st.caption('只创建本地模拟收款单；不会发送通知，也不会执行参与人的付款。')
    st.write(result_text(request, result))
    if result['status']=='succeeded' and request['action']=='card.apply':
        st.success('申请已受理，正在审核；受理不代表已发卡。编号：'+result['data']['application_id'])
    if result['status']=='succeeded' and request['action']=='merchant.order':
        st.success('模拟订单已提交。编号：'+result['data']['order_id'])
    if result['status']=='succeeded' and request['action']=='subscription.remind':
        st.caption('本地提醒已登记；请在订阅页面手动检查到期提醒，程序关闭时不会主动通知。')
    if result['status'] == 'succeeded' and request['action'] in ('budget.reserve','budget.release'):
        st.caption('预留编号：' + result['data']['reservation_id'])
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
