"""表单复用现有 Conversation 授权/恢复，不另建网关或自动确认。"""
import streamlit as st
from uuid import uuid4
from agent.controller import Conversation, reconcile_session_flows
from app import demo, ui
from shared import result_text, ServiceError, ContractError, adapt_request, validate_result
from shared.contracts import ACTIONS
from app.routing_panel import render_handoff
from app.money import yuan_text
from app.presentation import resource_name, approval_label, STATES, beijing, failure_note, receipt_details

LABELS = {'merchant.order':'商户下单', 'card.apply':'卡片申请受理', 'subscription.cancel':'取消订阅', 'subscription.remind':'登记续费提醒', 'card.freeze': '冻结', 'card.unfreeze': '解冻', 'card.report_loss': '挂失',
          'card.release_loss': '解挂', 'card.set_limit': '设置限额', 'transfer.split': 'AA 收款单',
          'subscription.list': '查看订阅', 'subscription.review': '核实或排除订阅',
          'subscription.scan': '扫描识别订阅',
          'transfer.schedule': '登记预约转账', 'transfer.create': '转账执行',
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
        # 其他页面可能已经确认/拒绝/过期收口原操作；先按权威记录对齐本页快照。
        flow.settle()
        reconcile_session_flows(st.session_state, flow)
        if flow.external_blocked:
            st.warning('其他页面仍有结果待核实，请先查询原操作；未知结果不能直接重新发起。')
        flow.refresh_operations()
        flow.resources = flow.gateway.get_context(principal_id=flow.principal_id)
        resume = st.session_state.pop('pending_resume', None)
        if isinstance(resume, dict) and resume.get('action') and resume.get('idempotency_key'):
            try:
                flow.recover(action=resume['action'], idempotency_key=resume['idempotency_key'])
            except (ValueError, ServiceError, ContractError):
                st.warning('未能恢复该原操作，请在待处理操作中查询原结果。')
        if st.session_state.get('routing_handoff'):
            render_handoff(st.session_state.routing_handoff)
        return flow
    except Exception:
        st.error('银行数据暂不可用，请检查原操作后再继续。')
        st.stop()


def perform(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except ValueError as exc:
        st.warning(str(exc))
        st.stop()
    except (ServiceError, ContractError) as exc:
        from app.auth_ui import error_text
        st.warning(error_text(exc))
        st.stop()
    st.rerun()


def render(flow, *, action_prefix=None):
    operations = [op for op in flow.operations if op['action'] in LABELS]
    with st.expander('恢复业务操作', expanded=bool(flow.unresolved) or (flow.blocked and not flow.request)):
        st.caption('这里只恢复原操作；恢复不会自动确认、验证或重新付款。')
        if operations:
            selected = st.selectbox('选择原操作', operations, key='action_history', format_func=lambda op:
                f"{LABELS[op['action']]} · {STATES.get(op['status'],op['status'])} · {op['idempotency_key']}")
            if st.button('恢复选中操作', key='action_recover'):
                perform(flow.recover, action=selected['action'], idempotency_key=selected['idempotency_key'])
        else:
            st.caption('暂无可恢复的业务操作。')
    if flow.blocked and not flow.request:
        st.warning('已有待处理或待核实的操作；请在上方“恢复业务操作”中查询、确认或拒绝后再继续。')
    if not flow.request or not flow.result:
        return
    request, result = flow.request, flow.result
    if (action_prefix and not request['action'].startswith(action_prefix)
            and result['status'] in {'succeeded','failed','cancelled','blocked'}
            and not flow.needs_reconciliation):
        return
    st.subheader('当前操作：' + LABELS.get(request['action'], request['action']))
    from app.presentation import render_authorization
    render_authorization(flow.gateway, request, principal_id=flow.principal_id)
    ui.render_status('needs_reconciliation' if result['status'] == 'unknown' else result['status'])
    from app.pending_view import _preview
    can_approve = _preview(flow)
    p = request['params']
    if request['action']=='wealth.purchase':
        st.write('付款账户：'+resource_name(flow,'account_id',p['account_id']))
        st.write('申购产品：'+resource_name(flow,'product_id',p['product_id']))
        st.write('申购金额：'+yuan_text(p['amount_minor'])+'（含申购费）')
        product=next((item for item in flow.resources['products'] if item['product_id']==p['product_id']),None)
        if product:
            st.write(product['name']+'；'+product['risk_level']+'；'+product['fee_description'])
            st.caption('锁定期：'+str(product['lock_days'])+'天；赎回条件：'+product['redemption_rule'])
        st.caption('核对本笔申购内容后需验证。')
    if request['action']=='wealth.redeem':
        st.write('到账账户：'+resource_name(flow,'account_id',p['account_id']))
        st.write('本笔赎回份额：'+p['units']+'份')
        st.caption('演示 T+1：验证成功后即时展示入账，不代表真实到账时间。')
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
            st.write('本期金额：'+yuan_text(details['amount_minor'])+'；对应扣费日：'+details['charge_date'])
        else:
            st.warning('无法核实原账户与支付卡绑定，请查询原操作并重新核对。')
        st.caption('支付卡由系统自动选择并绑定，不支持手选；本期续费需独立强验证，核实订阅不授予后续自动扣款权限。')
    if request['action']=='merchant.order':
        st.write(f"本笔金额：{p['amount_minor']/100:.2f}元；计划配送日：{p['deliver_on']}")
        item = next((row for row in flow.resources.get('items', []) if row['item_id']==p['item_id']), None)
        address = next((row for row in flow.resources.get('addresses', []) if row['delivery_address_id']==p['delivery_address_id']), None)
        st.write('商品：'+(item['name'] if item else '商品编号 '+p['item_id'])+'；地址：'+(address['label'] if address else '地址编号 '+p['delivery_address_id']))
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
        if 'remind_at' in p: st.write('提醒时间：'+beijing(p['remind_at']))
        else: st.caption('取消后不再派发未发送的本地提醒，已发送历史保留；不撤销已完成支付。')
    if request['action'] == 'budget.reserve':
        st.write('预留账户：' + resource_name(flow,'account_id',p['account_id']))
        st.write('预留金额：'+yuan_text(p['amount_minor'])+'；用途：'+p['purpose'])
        st.caption('确认后减少可用余额，账面余额不变；没有下单或付款。')
    if request['action'] == 'budget.release':
        st.write('预留编号：' + p['reservation_id'])
        rows = flow.gateway.list_reservations(principal_id=flow.principal_id)
        reservation = next((r for r in rows if r['reservation_id']==p['reservation_id']), None)
        if reservation:
            st.write('账户：'+reservation['account_id']+'；用途：'+reservation['purpose']+'；当前剩余预留：'+yuan_text(reservation['remaining_reserved_minor']))
        if flow.pending:
            try:
                amount=flow.gateway.release_quote(request,principal_id=flow.principal_id)
                st.write(f'当前可释放金额：{amount/100:.2f}元；确认时仍会复验。')
            except (ServiceError,ContractError):
                st.warning('预留状态已变化，请查询原操作后重新核对。')
        st.caption('待核实订单占用金额继续保留；只释放可核实的剩余金额。')
    if request['action'] in ('transfer.schedule','transfer.create'):
        st.write('付款账户：'+resource_name(flow,'account_id',p['account_id']))
        try:
            payee = flow.gateway.operation_payee(request,principal_id=flow.principal_id)
        except (ServiceError, ContractError):
            payee = None
        if payee:
            st.write('收款人：'+payee['display_name']+' · '+payee.get('bank_name','银行未提供')+' · '+payee['masked_account'])
        else:
            st.write('收款人：收款信息暂不可核实')
        st.write('金额：'+yuan_text(p['amount_minor']))
        if p.get('memo'):
            st.write('备注：'+p['memo'])
        if request['action']=='transfer.schedule':
            st.write('约定时间：'+beijing(p['execute_at']))
            st.caption('登记不扣款，到期另行确认；已派发不等于已付款。')
    if request['action'] == 'subscription.list':
        st.write('查询账户：' + resource_name(flow,'account_id',p['account_id']))
        st.caption('确认后才读取本次订阅列表。')
    if request['action'] == 'subscription.review':
        st.write('订阅：' + p['subscription_id'])
        st.write('本次选择：' + ('核实为订阅' if p['decision']=='accept' else '排除候选'))
        st.caption('核实不新签代扣授权；排除不取消既有扣费。')
    if 'card_id' in p and request['action'].startswith('card.'):
        names = {'CARD-E001': '白金卡', 'CARD-E002': '普通卡', 'CARD-E003': '挂失卡'}
        card_name = names.get(p['card_id'], '卡片')
        st.write('卡片：'+card_name+' · '+p['card_id'])
        st.caption('解挂后保持冻结，需要另行验证解冻。卡片状态尚未关联账户转账限制。')
        if request['action'] == 'card.set_limit':
            card = next((c for c in flow.resources['cards'] if c['card_id']==p['card_id']), None)
            if card:
                st.write('当前保存的限额：'+yuan_text(card['limit_minor'])+'；本次设置值：'+yuan_text(p['limit_minor']))
            st.caption('限额保存不改变卡状态；商户下单校验单笔限额；普通转账仍按自身规则处理。')
    if 'payee_ids' in p:
        st.write('收款账户：' + resource_name(flow,'account_id',p['account_id']))
        try:
            names={person['payee_id']:person['display_name']+' · '+person['masked_account'] for person in demo_payees(flow)}
        except (ServiceError,ContractError):
            names={}
        st.write('参与人顺序：' + ' → '.join(names.get(key,key) for key in p['payee_ids']))
        st.write('总金额：'+yuan_text(p['amount_minor']))
        st.caption('只创建本机收款单；不会发送通知，也不会执行参与人的付款。')
    st.write(result_text(request, result))
    for detail in receipt_details(request, result):
        st.text(detail)
    note = failure_note(result)
    if note: st.warning(note)
    if result['status']=='succeeded' and request['action']=='card.apply':
        st.success('申请已受理，正在审核；受理不代表已发卡。编号：'+result['data']['application_id'])
    if result['status']=='succeeded' and request['action']=='merchant.order':
        st.success('订单已提交。编号：'+result['data']['order_id'])
    if result['status']=='succeeded' and request['action']=='subscription.remind':
        st.caption('提醒已登记；启用后台后自动检查，也可手动检查，通知见站内待办。')
    if result['status'] == 'succeeded' and request['action'] in ('budget.reserve','budget.release'):
        st.caption('预留编号：' + result['data']['reservation_id'])
    if result['status'] == 'succeeded' and request['action'] == 'transfer.split':
        ui.render_records([{'参与人': names.get(s['payee_id'],s['payee_id']), '金额（元）': yuan_text(s['amount_minor'])}
                      for s in result['data']['shares']], title_field='参与人',
                      value_field='金额（元）', limit=None,
                      layout='activity', tone='core', hide_index=True)
        st.caption('收款单：' + result['data']['collection_id'])
    if flow.pending:
        verify = result['status'] == 'awaiting_verification'
        from app.auth_ui import transaction_code
        code=transaction_code(flow,key='action_totp') if verify else None
        st.caption('核对无误后确认；如需修改，请先取消本次操作再重新填写，旧授权会失效。')
        if st.button(approval_label(request, verify=verify, demo=flow.gateway.demo_mode), key='action_approve', type='primary', disabled=not can_approve):
            perform(flow.decide, True, simulate_verification=verify and flow.gateway.demo_mode,totp_code=code)
        if st.button('取消本次操作', key='action_reject'):
            perform(flow.decide, False)
    persisted = ACTIONS.get(request['action'], {}).get('write') or request['action'] in getattr(flow.gateway, 'confirmed_reads', set())
    if not persisted: st.caption('本次为只读查询，没有需要恢复的原操作编号。')
    if persisted and st.button('查询原操作结果', key='action_query'):
        perform(flow.recover)
    with st.expander('操作编号与原始请求'):
        st.caption('操作编号：' + request['idempotency_key'])
