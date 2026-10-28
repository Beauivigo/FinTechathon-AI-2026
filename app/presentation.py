"""Trusted display projection only; no authorization or money changes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from app.money import yuan_text

STATES = {'awaiting_confirmation':'待确认', 'awaiting_verification':'待身份验证',
          'running':'处理中', 'unknown':'结果待核实', 'needs_reconciliation':'结果待核实',
          'succeeded':'已完成', 'scheduled':'预约已保存', 'cancelled':'已取消',
          'failed':'未完成', 'blocked':'已锁定'}


def render_authorization(gateway, request, *, principal_id):
    """Display the original server requirement separately from execution status."""
    import streamlit as st
    from shared import ServiceError, ContractError
    try:
        requirement = gateway.authorization_requirement(request, principal_id=principal_id) if request else {}
    except (ServiceError, ContractError, OSError, AttributeError):
        requirement = {}
    level = requirement.get('risk_level')
    labels = {'green': '绿色 · 自动执行', 'yellow': '黄色 · 用户确认', 'red': '红色 · 强验证'}
    if level not in labels:
        st.caption('授权级别：暂不可核实，请查询原操作。')
        return
    label = labels[level]
    st.badge('授权级别：' + label, color={'green': 'green', 'yellow': 'orange', 'red': 'red'}[level])
    if level == 'red':
        methods = requirement.get('methods', [])
        method = {('totp',): '手机动态码', ('demo_otp',): '体验模拟验证'}.get(tuple(methods), '暂不可核实')
        st.caption('验证方式：' + method)
    if request['action'] in {'transfer.create', 'transfer.schedule'}:
        st.caption('按原操作的授权记录展示；日累计含本次1000元为确认门槛，执行前仍会重验，强验证要求不会跨日降低。')


def beijing(value):
    if not value:
        return '未提供'
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            return str(value) + '（时区未提供）'
        return parsed.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') + ' 北京时间'
    except ValueError:
        return str(value)


def resource_name(flow, key, value):
    resources = flow.resources
    mapping = {'account_id':('accounts','account_id','display_name'),
               'card_id':('cards','card_id','display_name'),
               'product_id':('products','product_id','name'),
               'item_id':('items','item_id','name'),
               'delivery_address_id':('addresses','delivery_address_id','label')}
    if key not in mapping:
        return str(value)
    group, id_field, name_field = mapping[key]
    row = next((r for r in resources.get(group, []) if r[id_field] == value), None)
    if row is None:
        return str(value) + '（名称暂不可核实）'
    # Historical cards may contain only an internal ID; never invent a tail.
    from bank.resource_labels import card_name
    name = row.get(name_field) or row.get('name') or (card_name(value) if key == 'card_id' else str(value))
    return name + (' · '+row['masked_account'] if row.get('masked_account') else '')


def approval_label(request, *, verify=False, demo=False):
    action, p = request['action'], request['params']
    verbs = {'transfer.create':'转账', 'transfer.schedule':'保存预约', 'transfer.split':'创建收款单',
             'budget.reserve':'预留', 'budget.release':'释放可核实余额',
             'merchant.order':'下单', 'wealth.purchase':'申购', 'wealth.redeem':'赎回',
             'card.freeze':'冻结卡片', 'card.unfreeze':'解冻卡片', 'card.report_loss':'挂失卡片',
             'card.release_loss':'解除挂失', 'card.set_limit':'设置限额', 'card.apply':'提交申请',
             'subscription.list':'读取本次订阅', 'subscription.review':'提交核实结果',
             'subscription.cancel':'取消订阅', 'subscription.remind':'保存提醒',
             'subscription.charge':'办理本期续费', 'subscription.scan':'识别候选'}
    verb = verbs.get(action, '办理本次操作')
    amount = p.get('amount_minor', p.get('limit_minor'))
    if amount is not None:
        verb += ' ' + yuan_text(amount)
    if action == 'wealth.redeem':
        verb += ' ' + p['units'] + '份'
    prefix = ('体验验证并' if demo else '验证并') if verify else '确认'
    return prefix + verb


def cancel_transfer_for_edit(flow):
    """End the old authorization before releasing a replacement draft."""
    from copy import deepcopy
    if not flow.request or flow.request['action'] != 'transfer.create' or not flow.pending:
        raise ValueError('仅可修改待确认或待身份验证的转账；处理中或结果未知请核实原操作。')
    params = deepcopy(flow.request['params'])
    flow.decide(False)
    flow.recover()
    if not flow.result or flow.result['status'] != 'cancelled' or flow.needs_reconciliation:
        raise ValueError('尚未确认旧操作已取消，请查询原操作，不能生成替代转账。')
    # Retain the authoritative old operation in history; only clear the UI snapshot.
    flow.request = flow.result = flow.backend_result = flow.payee = None
    flow.draft, flow.candidates = None, []
    return params


# Reviewed error text for the shared result area. Only whitelisted codes are
# shown; arbitrary backend messages are never echoed to the user.
FAILURE_GUIDANCE = {
    'INSUFFICIENT_FUNDS': ('可用余额不足，本次未执行。', '可核对余额与预留后减少金额；不会自动重试。'),
    'RISK_BLOCKED': ('风险等级与产品不匹配，本次未执行。', '可重新评估风险或改选匹配的产品。'),
    'LIMIT_EXCEEDED': ('金额、份额或业务范围不满足本次限制。', '请核对起购金额、可用份额和页面限额；不会自动拆分或重试。'),
    'INVALID_STATE': ('当前状态不允许该操作，本次未执行。', '请刷新并核对对象状态后重新发起。'),
    'FORBIDDEN': ('无权使用该账户、卡片或联系人，本次未执行。', '请核对登录身份与资源归属。'),
    'AUTH_EXPIRED': ('原授权已过期，本次未执行。', '仍需办理时请重新发起；已有结果只以查询为准。'),
    'AUTH_INVALID': ('授权或验证未通过，本次未执行。', '请重新核对；连续失败会锁定本次操作。'),
    'SAFETY_LOCKED': ('验证失败次数过多，本次操作已锁定。', '请核实原操作结果，不要重复提交。'),
    'IDEMPOTENCY_CONFLICT': ('原操作编号对应的内容不一致。', '请先查询原操作，不要换键重试。'),
    'NOT_FOUND': ('暂未找到原操作记录。', '查无记录不能证明未执行，请保留编号继续核实。'),
    'UPSTREAM_ERROR': ('银行数据暂时不可用，本次未确认成功。', '请稍后查询原操作，不要重复发起。'),
    'RESULT_UNKNOWN': ('执行结果未知。', '请查询原操作；查无记录也不能证明未扣款。'),
    'INVALID_REQUEST': ('提交内容不符合银行要求，本次未执行。', '请核对金额、对象和必填项。'),
}


def failure_note(result):
    """Short Chinese reason plus a lawful next step for a reviewed error code."""
    code = ((result or {}).get('error') or {}).get('code')
    if not code:
        return ''
    reason, next_step = FAILURE_GUIDANCE.get(
        code, ('本次操作未完成。', '请核对输入；结果不明时先查询原操作。'))
    return reason + next_step


def receipt_details(request, result):
    """Useful facts from validated bank receipts, without exposing raw JSON."""
    from shared import validate_result
    validate_result(request, result)
    if result['status'] != 'succeeded':
        return []
    data, action = result['data'], request['action']
    if action == 'account.balance':
        return ['可用余额：' + yuan_text(data['available_minor']),
                '账面余额：' + yuan_text(data['balance_minor']) + ' · 已预留：' + yuan_text(data['reserved_minor'])]
    if action == 'bill.analyze':
        return ['消费与订阅支出：' + yuan_text(data['total_expense_minor']),
                '异常候选：' + str(len(data['anomalies'])) + '项（仅覆盖已启用的检测规则）']
    if action == 'wealth.compare':
        return [p['name'] + ' · ' + p['risk_level'] + ' · 起购' + yuan_text(p['minimum_amount_minor'])
                for p in data['products']]
    if action == 'wealth.assess':
        return ['本次风险评估：' + data['risk_level']]
    if action in {'wealth.purchase', 'budget.reserve', 'merchant.order'}:
        return ['本次金额：' + yuan_text(data['amount_minor'])]
    if action == 'wealth.redeem':
        return ['本次赎回份额：' + data['units'] + '份']
    if action == 'subscription.list':
        return ['本次读取' + str(len(data['subscriptions'])) + '条订阅；查询未扣款。']
    if action == 'subscription.charge':
        return ['本期续费：' + yuan_text(data['charged_minor']) + ' · 下次扣费日：' + data['next_charge_date']]
    return []


CARD_STATE_LABELS = {'active': '正常', 'frozen': '已冻结', 'lost': '已挂失'}
CARD_ACTION_LABELS = {'card.freeze': '冻结', 'card.unfreeze': '解冻', 'card.report_loss': '挂失',
                      'card.release_loss': '解挂', 'card.set_limit': '设置限额'}
# Mirrors shared.services.validate_card_change; the server still re-validates.
CARD_ACTION_ORIGINS = {'card.freeze': {'active'}, 'card.unfreeze': {'frozen'},
                       'card.report_loss': {'active', 'frozen'}, 'card.release_loss': {'lost'}}


def card_available_actions(state):
    """Actions the card state machine allows, always including limit editing."""
    return [action for action, origins in CARD_ACTION_ORIGINS.items()
            if state in origins] + ['card.set_limit']


def card_unavailable_reasons(state):
    reasons = []
    if state not in CARD_ACTION_ORIGINS['card.freeze']:
        reasons.append('冻结仅适用于正常卡；已冻结或挂失的卡无需再次冻结。')
    if state not in CARD_ACTION_ORIGINS['card.unfreeze']:
        reasons.append('解冻仅适用于已冻结的卡。')
    if state not in CARD_ACTION_ORIGINS['card.report_loss']:
        reasons.append('已挂失的卡不能再次挂失。')
    if state not in CARD_ACTION_ORIGINS['card.release_loss']:
        reasons.append('解挂仅适用于已挂失的卡。')
    return reasons


SUBSCRIPTION_STATE_LABELS = {'active': '有效', 'candidate': '待核实候选',
                             'cancelled': '已取消', 'dismissed': '已排除', 'rejected': '已排除'}


def subscription_state_label(state):
    return SUBSCRIPTION_STATE_LABELS.get(state, state)


def subscription_snapshot(result, *, queried_at):
    """Session-only snapshot of one confirmed yellow read, stamped with its time."""
    rows = ((result or {}).get('data') or {}).get('subscriptions')
    if not isinstance(rows, list):
        return None
    return {'queried_at': queried_at, 'rows': deepcopy(rows)}


def apply_subscription_state(snapshot, subscription_id, state, *, next_charge_date=None):
    """Update the held snapshot from an authoritative management receipt."""
    if not isinstance(snapshot, dict):
        return snapshot
    for row in snapshot.get('rows', []):
        if row.get('subscription_id') == subscription_id:
            row['subscription_state'] = state
            if next_charge_date:
                row['next_charge_date'] = next_charge_date
    return snapshot


SCHEDULE_STATES = {'scheduled': '待到期', 'running': '处理中', 'dispatched': '已派发',
                   'failed': '未完成', 'unknown': '结果待核实', 'cancelled': '已取消'}


def schedule_display(task_state, operation_status):
    """Separate the appointment's own state from the original payment result."""
    if task_state == 'dispatched':
        if operation_status == 'succeeded':
            return ('付款已完成', '结果来自账本回执，无需再次确认。')
        if operation_status == 'awaiting_confirmation':
            return ('已派发 · 待你确认付款', '后台已准备付款待办，确认前不会扣款。')
        if operation_status == 'awaiting_verification':
            return ('已派发 · 待身份验证', '后台已准备付款待办，验证前不会扣款。')
        if operation_status in {'unknown', 'running', 'UNKNOWN', 'NOT_FOUND', None}:
            return ('付款结果待核实', '请查询并恢复此任务的付款操作，不要重复登记。')
        if operation_status in {'failed', 'blocked', 'cancelled'}:
            return ('付款未完成', '该预约没有被扣款；需要时请核对后重新登记。')
        return ('已派发 · 待准备付款', '执行键已建立但尚未返回可靠付款状态，请稍后查询。')
    label = SCHEDULE_STATES.get(task_state, task_state)
    hints = {'scheduled': '预约只登记；到点后仍需你确认或验证。',
             'running': '后台正在处理，不会自动完成付款确认。',
             'unknown': '请查询原操作或本页的付款结果。',
             'failed': '预约未完成；未领取任务迟到超过5分钟会失效。',
             'cancelled': '取消预约只停止尚未领取的任务，不代表退款。'}
    return (label, hints.get(task_state, ''))


MODEL_ERROR_GUIDANCE = (
    (('无效', '不匹配', '格式无效'), '模型配置无效。', '请在“我的—高级设置”核对服务商、模型和密钥后重试。'),
    (('未配置', '请先配置', '缺少密钥'), '尚未配置可用模型。', '请到“我的—高级设置”保存密钥，或先使用手动业务入口。'),
    (('余额不足',), '模型账户余额不足。', '请充值或更换已开通的服务商；不会自动重试。'),
    (('限流',), '模型服务暂时限流。', '请稍后手动重试；不会自动重试或换键。'),
    (('超时', '不可用'), '模型连接超时或不可用。', '请检查网络与配置后手动重试；本次没有生成新的银行操作。'),
    (('JSON', '回复过长', '上下文'), '模型未返回可执行的草稿。', '可补充或简化需求后重试；银行原操作不受影响。'),
)


def model_error_guidance(message):
    """Classify only the reviewed local model errors; never echo raw backend text."""
    if not message:
        return ''
    for needles, reason, next_step in MODEL_ERROR_GUIDANCE:
        if any(needle in str(message) for needle in needles):
            return reason + next_step
    return '模型调用未完成。请检查模型设置后手动重试；已提交的银行操作须查询原结果。'


HANDOFF_DESTINATIONS = {
    'transfer': ('transfer', '手动办理：转账与收款'),
    'scheduled_transfer': ('scheduled', '手动办理：预约转账'),
    'aa': ('aa', '手动办理：AA 收款单'),
    'card': ('cards', '手动办理：卡片管理'),
    'subscription': ('subscriptions', '手动办理：订阅管理'),
    'birthday': ('merchant', '手动办理：生日计划'),
    'budget_reserve': ('budget', '手动办理：预算管理'),
    'budget_release': ('budget', '手动办理：预算管理'),
    'merchant_order': ('merchant', '手动办理：生日与采购'),
    'wealth_assess': ('wealth', '手动办理：风险评估'),
    'wealth_purchase': ('wealth', '手动办理：理财申购'),
    'wealth_redeem': ('wealth', '手动办理：理财赎回'),
    'wealth_holdings': ('wealth', '手动办理：理财持仓'),
    'products': ('products', '手动办理：产品对比'),
    'bill': ('bills', '手动办理：收支明细'),
    'balance': ('home', '手动办理：账户首页'),
}


def handoff_destinations(intent):
    """Accurate manual entry for the recognised intent; never rebuilds a request."""
    key, label = HANDOFF_DESTINATIONS.get(intent, ('home', '手动办理：返回首页'))
    return [(key, label)]


FIELD_LABELS = {
    'account_id': '付款或业务账户', 'payee_query': '收款人', 'amount_text': '金额',
    'period': '账单期间', 'execute_at': '预约时间', 'participants': 'AA 参与人',
    'card_operation': '卡片操作', 'card_id': '银行卡', 'limit_text': '单笔限额',
    'subscription_operation': '订阅操作', 'subscription_id': '订阅',
    'remind_at': '提醒时间', 'product_ids': '产品', 'product_id': '产品',
    'questionnaire_version': '风险问卷版本', 'answers': '风险问卷答案',
    'risk_profile_id': '风险档案', 'holding_id': '持仓', 'units_text': '赎回份额',
    'birthday': '生日日期', 'purpose': '预留用途', 'reservation_id': '预算预留',
    'item_id': '商品', 'deliver_on': '配送日期', 'delivery_address_id': '配送地址',
    'memo': '备注', 'budget_text': '预算金额', 'item_ids': '商品', 'budget_at': '预算处理时间',
    'purchase_at': '采购处理时间',
}


def missing_text(entries):
    """Human-readable missing list from the bounded session state."""
    labels = []
    for entry in entries or ():
        field = str(entry).split(':')[-1]
        label = FIELD_LABELS.get(field, '补充信息')
        if label not in labels:
            labels.append(label)
    return '、'.join(labels)


def dialogue_controls(phase, *, allow_back=True):
    """Pure control policy so pausing/handoff never leaves a dead action."""
    usable = phase not in {'idle', 'paused', 'handoff', 'locked'}
    return {
        'send': phase not in {'paused', 'handoff', 'locked'},
        'pause': usable,
        'resume': phase == 'paused',
        'back': usable and bool(allow_back),
        'handoff': phase not in {'idle', 'handoff', 'locked'},
        'clear': phase != 'idle',
    }


def dialogue_note(phase, write_blocked=False):
    """Explain the current gating in plain Chinese without implying an execution."""
    if phase == 'paused':
        return '会话已暂停：先点击“继续草稿”再发送；已提交的银行操作不会因暂停而撤销。'
    if phase == 'handoff':
        return '会话已改为手动处理：请使用下方业务入口，已有银行操作请先核实原结果。'
    if phase == 'locked':
        return '本会话已停止模型推进：请核实原操作后点击“新需求”重新开始。'
    if write_blocked:
        return '存在待处理或待核实的原操作：你仍可提问、补充和修改草稿；新的业务提交需先处理待办。'
    return ''

