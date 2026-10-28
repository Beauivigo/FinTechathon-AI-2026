"""共享的“待处理操作”入口：只驱动既有确认、拒绝、恢复与验证流程。

页面不在这里直接写银行账本，也不放宽任何门槛：所有按钮都调用
agent.controller.Conversation 的原有 decide/recover 通道，保留归属、
操作摘要、过期、未知结果与强验证规则。清单只来自会话最近一次刷新的
权威操作索引，不读取已结束的历史页面对象。
"""

from agent.controller import UNRESOLVED

ACTION_LABELS = {
    'transfer.create': '转账',
    'transfer.schedule': '预约转账',
    'transfer.split': 'AA 收款单',
    'card.apply': '卡片申请',
    'card.freeze': '冻结卡片',
    'card.unfreeze': '解冻卡片',
    'card.report_loss': '挂失卡片',
    'card.release_loss': '解挂卡片',
    'card.set_limit': '设置限额',
    'subscription.list': '查看订阅',
    'subscription.review': '核实或排除订阅',
    'subscription.scan': '扫描识别订阅',
    'subscription.cancel': '取消订阅',
    'subscription.remind': '登记续费提醒',
    'subscription.charge': '本期订阅续费',
    'merchant.order': '商户下单',
    'budget.reserve': '预留预算',
    'budget.release': '释放预算',
    'wealth.assess': '风险评估',
    'wealth.purchase': '理财申购',
    'wealth.redeem': '理财赎回',
    'bill.analyze': '账单分析',
}

STATUS_LABELS = {
    'awaiting_confirmation': '待确认',
    'awaiting_verification': '待身份验证',
    'running': '处理中',
    'unknown': '结果待核实',
}

PAGE_BY_ACTION = {
    'transfer.create': 'main.py',
    'transfer.schedule': 'pages/6_定时转账.py',
    'transfer.split': 'pages/4_AA收款.py',
    'card.apply': 'pages/3_卡片管理.py',
    'card.freeze': 'pages/3_卡片管理.py',
    'card.unfreeze': 'pages/3_卡片管理.py',
    'card.report_loss': 'pages/3_卡片管理.py',
    'card.release_loss': 'pages/3_卡片管理.py',
    'card.set_limit': 'pages/3_卡片管理.py',
    'subscription.list': 'pages/5_订阅管理.py',
    'subscription.review': 'pages/5_订阅管理.py',
    'subscription.scan': 'pages/5_订阅管理.py',
    'subscription.cancel': 'pages/5_订阅管理.py',
    'subscription.remind': 'pages/5_订阅管理.py',
    'subscription.charge': 'pages/5_订阅管理.py',
    'merchant.order': 'pages/8_生日与商户.py',
    'budget.reserve': 'pages/7_预算管理.py',
    'budget.release': 'pages/7_预算管理.py',
    'wealth.assess': 'pages/9_理财.py',
    'wealth.purchase': 'pages/9_理财.py',
    'wealth.redeem': 'pages/9_理财.py',
}


def _switch_to(flow, action, key):
    import streamlit as st
    st.session_state['pending_resume'] = {'action': action, 'idempotency_key': key}
    if action == 'transfer.create':
        st.session_state['home_next_tab'] = '转账与收款'
        st.switch_page('main.py')
        return
    path = PAGE_BY_ACTION.get(action)
    if path is None:
        st.info('未找到该业务的原页面，请在操作记录中查询原操作。')
        return
    st.switch_page(path.removeprefix('app/'))


def action_label(action):
    return ACTION_LABELS.get(action, action)


def status_label(status):
    return STATUS_LABELS.get(status, status)


def operation_key(request):
    return request.get('idempotency_key', request['request_id'])


def unresolved_items(flow):
    """返回权威索引中的未决操作；纯展示数据，不改变任何业务状态。"""
    if flow is None:
        return ()
    return tuple(
        dict(action=op['action'], key=op['idempotency_key'], status=op['status'],
             action_label=action_label(op['action']), status_label=status_label(op['status']))
        for op in flow.operations if op['status'] in UNRESOLVED
    )


def local_unresolved(flow):
    """本页会话内存里仍待确认/待核实的结果；用于未知结果也必须可见。"""
    if flow is None:
        return False
    if flow.needs_reconciliation or flow.pending:
        return True
    return bool(flow.result and flow.result['status'] in UNRESOLVED)


def needs_attention(flow):
    return local_unresolved(flow) or bool(unresolved_items(flow))


def _flash_message(request, result):
    from shared import result_text
    from app.presentation import failure_note
    text = result_text(request, result)
    note = failure_note(result)
    if note and note not in text:
        text = text + ' ' + note
    return text


def _brief(flow, item):
    """Read original metadata without approving a pending confirmed read."""
    from uuid import uuid4
    from shared import adapt_request, validate_result, ServiceError, ContractError
    from app.money import yuan_text
    from app.presentation import resource_name, beijing
    base=item['action_label']+' · '+item['status_label']
    try:
        if item['action']=='subscription.list':
            record=flow.gateway.get_confirmed_read(item['key'],principal_id=flow.principal_id)
        else:
            request=adapt_request('operation.get',dict(original_action=item['action'],original_idempotency_key=item['key']),
                request_id='view_'+uuid4().hex,trace_id='pending_'+uuid4().hex)
            result=validate_result(request,flow.gateway.execute(request,principal_id=flow.principal_id))
            if result['status']!='succeeded':return base+' · 信息暂不可核实'
            record=result['data']
        original=record['operation_request'];params=original['params'];parts=[]
        if original['action'] in {'transfer.create','transfer.schedule'}:
            person=flow.gateway.operation_payee(original,principal_id=flow.principal_id)
            if person:parts.append(person['display_name']+' '+person['masked_account'])
        elif params.get('product_id'):parts.append(resource_name(flow,'product_id',params['product_id']))
        elif params.get('purpose'):parts.append(params['purpose'])
        elif params.get('account_id'):parts.append(resource_name(flow,'account_id',params['account_id']))
        if 'amount_minor' in params:parts.append(yuan_text(params['amount_minor']))
        if 'units' in params:parts.append(params['units']+'份')
        if params.get('execute_at'):parts.append(beijing(params['execute_at']))
        return base+' · '+' · '.join(parts or ['查看原操作详情'])
    except (ValueError,ServiceError,ContractError):
        return base+' · 信息暂不可核实'


def _act(flow, call, flash_key):
    """执行原确认/拒绝/查询动作；失败文案保留到下一次重跑后展示。"""
    import streamlit as st
    from app.auth_ui import error_text
    from shared import ContractError, ServiceError
    message = None
    try:
        outcome = call()
        if isinstance(outcome, dict) and 'status' in outcome and flow.request is not None:
            message = _flash_message(flow.request, outcome)
    except (ValueError, ContractError, ServiceError) as exc:
        message = str(exc) if type(exc) is ValueError else error_text(exc)
    if message:
        st.session_state[flash_key] = message
    st.rerun()


def _preview(flow):
    """确认前展示服务端保存的原参数；核对失败时只能拒绝或查询。"""
    import streamlit as st
    from shared import ContractError, ServiceError
    from app.money import yuan_text
    from app.presentation import resource_name, beijing
    labels = {'account_id':'业务账户','product_id':'理财产品','item_id':'商品',
        'card_id':'卡片','delivery_address_id':'配送地址','deliver_on':'配送日期',
        'execute_at':'预约时间','remind_at':'提醒时间','purpose':'用途','memo':'备注','units':'赎回份额'}
    request = flow.request
    for key, value in request['params'].items():
        if key in {'amount_minor', 'limit_minor'}:
            st.text(('本笔金额' if key=='amount_minor' else '单笔限额')+'：'+yuan_text(value))
        elif key in labels:
            display = beijing(value) if key in {'execute_at','remind_at'} else resource_name(flow,key,value)
            st.text(labels[key]+'：'+display+('份' if key=='units' else ''))
    with st.expander('原操作编号与详细参数'):
        st.caption('操作编号：'+operation_key(request))
        st.json(request['params'])
    try:
        if request['action'] in {'transfer.create','transfer.schedule'}:
            payee=flow.gateway.operation_payee(request,principal_id=flow.principal_id)
            if not payee:raise ValueError('payee unavailable')
            st.text('收款人：'+payee['display_name']+' · '+payee['masked_account'])
        if request['action']=='budget.release':
            amount=flow.gateway.release_quote(request,principal_id=flow.principal_id)
            st.text(f'当前可释放金额：{amount//100}.{amount%100:02d}元；确认时仍会复验。')
        if request['action']=='subscription.charge':
            details=flow.gateway.subscription_charge_details(request,principal_id=flow.principal_id)
            if not details:raise ValueError('charge binding unavailable')
            amount=details['amount_minor']
            st.text(f"本期金额：{amount//100}.{amount%100:02d}元；原支付卡：{details['card_id']}")
    except (ValueError,ContractError,ServiceError):
        st.warning('原操作的对象或金额暂不可核实，暂不允许确认；请查询原结果，或拒绝本次操作。')
        return False
    return True


def render_unresolved(flow, *, key_prefix, note=None, include_local=True):
    """在阻塞发生的位置显示待办与可用处理动作；无待办时返回 False。"""
    import streamlit as st
    if flow is None:
        return False
    flash = st.session_state.pop(key_prefix + '_flash', None)
    items = unresolved_items(flow)
    local = local_unresolved(flow)
    if not items and not local and flow.external_blocked:
        shown = False
        for index,key in enumerate(('conversation','action_flow','model_bank_flow')):
            other = st.session_state.get(key)
            if other is not None and other is not flow and local_unresolved(other):
                shown = render_unresolved(other,key_prefix=key_prefix+'_source_'+str(index),
                    note='另一页面保留了待核实原操作，请在这里查询原结果。') or shown
        if shown:return True
    if not items and not local:
        # 刚完成确认/拒绝/查询后仍给出一次明确回执，然后自然收起待办区。
        if flash:
            st.info(flash)
            return True
        return False
    st.subheader('待处理操作')
    if note:
        st.caption(note)
    if flash:
        st.info(flash)
    if items:
        items=tuple(dict(item,display=_brief(flow,item)) for item in items)
        st.warning(f'有 {len(items)} 项业务尚未结束；确认、拒绝或核实原结果后才能继续办理新的业务。')
        selected = st.selectbox(
            '选择待处理操作', items, key=key_prefix + '_select',
            format_func=lambda item:item['display'],
        )
        if st.button('查看并处理待办', key=key_prefix + '_open', width='stretch'):
            _act(flow, lambda: flow.recover(action=selected['action'],
                                            idempotency_key=selected['key']),
                 key_prefix + '_flash')
        if st.button('前往原业务页面', key=key_prefix + '_goto', width='stretch'):
            _switch_to(flow, selected['action'], selected['key'])
    else:
        st.warning('本页仍有未核实的原操作；请查询权威结果后再继续，不要重复提交。')
    request, result = flow.request, flow.result
    if include_local and request is not None and result is not None and local:
        from app.presentation import render_authorization
        render_authorization(flow.gateway, request, principal_id=flow.principal_id)
        # 本地快照仍显示未决：只在原操作确实可确认/可拒绝时给出按钮。
        st.caption('执行状态：' + status_label(result['status']))
        st.caption('原操作：' + action_label(request['action']) + ' · '
                   + status_label(result['status']) + ' · ' + operation_key(request))
        can_approve = _preview(flow)
        if result['status'] == 'awaiting_confirmation':
            from app.presentation import approval_label
            if st.button(approval_label(request), key=key_prefix + '_approve', type='primary', width='stretch', disabled=not can_approve):
                _act(flow, lambda: flow.decide(True), key_prefix + '_flash')
        elif result['status'] == 'awaiting_verification':
            from app.auth_ui import transaction_code
            from app.presentation import approval_label
            code = transaction_code(flow, key=key_prefix+'_totp')
            if flow.gateway.demo_mode:
                st.caption('体验验证仅用于示例账本；设置本机账号后使用手机验证器。')
            if st.button(approval_label(request, verify=True, demo=flow.gateway.demo_mode),
                         key=key_prefix+'_verify', type='primary', width='stretch', disabled=not can_approve):
                _act(flow, lambda: flow.decide(True, simulate_verification=flow.gateway.demo_mode, totp_code=code),
                     key_prefix+'_flash')
        if result['status'] in {'awaiting_confirmation', 'awaiting_verification'}:
            if st.button('取消本次操作', key=key_prefix + '_reject', width='stretch'):
                _act(flow, lambda: flow.decide(False), key_prefix + '_flash')
        if st.button('查询待办结果', key=key_prefix + '_query', width='stretch'):
            _act(flow, lambda: flow.recover(), key_prefix + '_flash')
    return True
