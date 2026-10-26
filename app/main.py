"""启动：python -m streamlit run app/main.py。"""

from pathlib import Path
import sys
from datetime import date, datetime, timedelta, timezone
from calendar import monthrange
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from agent.controller import Conversation, reconcile_session_flows
from bank.gateway import BankGateway
from bank.seed import initialize, demo_path, reset_demo
from bank.store import Store
from shared import ContractError, ServiceError, result_text, adapt_request, validate_result
from app.routing_panel import render_router
from app import ui, home_view, onboarding, pending_view


st.set_page_config(page_title="银行助手", page_icon=":material/account_balance:", layout="wide")
display_tab = st.session_state.get('home_next_tab') or st.session_state.get('home_workspace')
display_key = {'转账汇款': 'transfer', '账单流水': 'bills'}.get(display_tab, 'home')
display_query = st.query_params.get('view')
if display_query in {'home', 'transfer', 'bills'}:
    display_key = display_query
from app.auth_ui import error_text, gate
gate()
ui.render_sidebar_nav(display_key)


def new_conversation():
    store = Store(demo_path())
    initialize(store)  # 只在首次导入；再次打开保留业务记录。
    from app.demo import gateway
    return Conversation(gateway(), principal_id='demo_user_a')


if "conversation" not in st.session_state:
    try:
        st.session_state.conversation = new_conversation()
    except Exception:
        st.error('银行数据暂时无法打开。请检查数据库，不要删除数据库来绕过核查。')
        st.stop()
conversation = st.session_state.conversation
other_flow = st.session_state.get('action_flow')
reconcile_session_flows(st.session_state, conversation)
if conversation.external_blocked:
    st.warning('其他业务的结果未知或尚待核实；可在本页“待处理操作”或对应页面查询原操作。')
operations_available = True
try:
    # 其他页面/会话可能已经确认、拒绝或使原授权过期；先按权威记录收口本页快照。
    conversation.settle()
    conversation.refresh_operations()
except ServiceError:
    operations_available = False
    st.warning('暂时无法刷新操作列表，请稍后重试。')


def perform(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except (ValueError, ContractError, ServiceError) as exc:
        # 用户文案不直接暴露后端错误详情；验证码/过期/锁定给出可执行提示。
        message = str(exc) if type(exc) is ValueError else error_text(exc)
        conversation.messages.append({"role": "assistant", "content": message})
    st.session_state.home_next_tab = "转账汇款"
    st.rerun()


def query(action, params):
    request = adapt_request(action, params, request_id='view_'+uuid4().hex, trace_id='page_'+uuid4().hex)
    result = validate_result(request, conversation.gateway.execute(request, principal_id=conversation.principal_id))
    if result['status'] != 'succeeded':
        raise ServiceError(result.get('error', {}).get('code', 'UPSTREAM_ERROR'), '查询暂不可用。')
    return result['data']


def money(amount):
    return f'¥ {amount // 100:,}.{amount % 100:02d}'


balance = None
with st.sidebar:
    with st.expander('转账示例'):
        st.subheader("试一笔转账")
        for index, example in enumerate(("给李明转200元", "给小王转钱", "给小王转两百", "给李明转1200元")):
            if st.button(example, key=f"example_{index}", disabled=conversation.blocked, width="stretch"):
                perform(conversation.submit, example)
        st.caption("小王有两个同名账户，需要根据尾号选择。")
    st.divider()
    with st.expander('账户设置'):
        st.caption("日常账户")
        try:
            balance = query('account.balance', {'account_id': 'demo_account_a'})
            st.metric('可用余额', money(balance['available_minor']))
            st.caption('余额保存在本地，刷新不会重置。')
        except (ContractError, ServiceError):
            st.warning('余额暂不可用。')
        if st.button("新建对话", disabled=conversation.blocked, width="stretch"):
            st.session_state.conversation = new_conversation()
            st.rerun()
        st.caption('新建对话只清空聊天草稿，不改变余额和流水。')
        with st.expander('重置本地数据'):
            st.write('会清除全部业务、授权和流水，恢复 2000 元初始余额。')
            confirmed = st.checkbox('我确认清除本机数据', disabled=conversation.blocked)
            if st.button('确认重置模拟银行', disabled=conversation.blocked or not confirmed):
                try:
                    current = conversation.gateway.list_operations(principal_id=conversation.principal_id)
                    if any(op['status'] in pending_view.UNRESOLVED for op in current):
                        raise ValueError('请先处理或核实全部待办，再重置。')
                    reset_demo(confirm=True)
                    st.session_state.pop('action_flow', None)
                    st.session_state.conversation = new_conversation()
                    st.rerun()
                except (ValueError, ServiceError):
                    st.error('重置未完成。请先处理或核实全部待办。')
    st.divider()
    from app.background_panel import render_background
    render_background(conversation)


# Tabs only select a view. All existing business guards and callbacks remain below.
tab_labels = ('账户首页', '转账汇款', '账单流水', '操作记录')
requested_view = st.query_params.pop('view', None)
view_tabs = {'home': '账户首页', 'transfer': '转账汇款', 'bills': '账单流水', 'history': '操作记录'}
if isinstance(requested_view, str) and requested_view in view_tabs:
    st.session_state.home_next_tab = view_tabs[requested_view]
next_tab = st.session_state.pop('home_next_tab', None)
if next_tab in tab_labels:
    st.session_state.home_workspace = next_tab
home_tab, transfer_tab, ledger_tab, history_tab = st.tabs(
    tab_labels, key='home_workspace', on_change='rerun',
)
with home_tab:
    home_area = st.container()
recent_rows = None

with transfer_tab:
    st.title('把一笔转账说清楚')
    st.caption('先填写收款人和金额，再核对并确认。')
    if conversation.blocked and not conversation.request:
        st.warning('已有待处理或待核实的操作；请先到“账户首页”的“待处理操作”恢复并确认、拒绝或查询，未知结果必须先核实。')
    chat, details = st.columns([1.5, 1], gap="large")
    with chat:
        with ui.business_section("对话", description="告诉我收款人和金额，信息不完整时会先向你确认。"):
            if not conversation.messages:
                with st.chat_message("assistant"):
                    st.write("可以说“给李明转200元”。也可以先说“给小王转钱”，再补充金额。")
            for message in conversation.messages:
                with st.chat_message(message["role"]):
                    st.write(message["content"])
            if conversation.candidates and not conversation.request:
                st.write("请选择收款账户")
                for candidate in conversation.candidates:
                    label = f"{candidate['display_name']} · {candidate.get('bank_name', '银行未提供')} · {candidate['masked_account']}"
                    if st.button(label, key="payee_" + candidate["payee_id"], width="stretch"):
                        perform(conversation.select_payee, candidate["payee_id"])
            prompt = st.chat_input("例如：给小王转200元", key="transfer_input", max_chars=200, disabled=conversation.blocked)
            if prompt:
                perform(conversation.submit, prompt)

    with details:
        with ui.business_section("当前操作"):
            request, result = conversation.request, conversation.result
            if result:
                ui.render_status('needs_reconciliation' if result['status'] == 'unknown' else result['status'])
            else:
                ui.render_status('draft', detail='先准备草稿，核对后才会进入确认。')
            if request and result and request['action']=='transfer.create':
                amount = request["params"]["amount_minor"]
                st.metric("转账金额", f"¥ {amount // 100:,}.{amount % 100:02d}")
                st.write("收款人：" + conversation.payee["display_name"])
                st.write("收款账户：" + conversation.payee["masked_account"])
                st.write("银行：" + conversation.payee.get("bank_name", "未提供"))
                st.divider()
                text = result_text(request, result)
                if result["status"] == "succeeded":
                    st.success(text)
                    st.caption("结果来自已提交的账本。")
                elif result["status"] in {"awaiting_confirmation", "awaiting_verification"}:
                    st.warning(text)
                    if result["status"] == "awaiting_verification":
                        st.write("含本次的上海当日累计超过1000元，需模拟强验证；验证要求不会跨日降级。" if conversation.gateway.demo_mode else "含本次的上海当日累计超过1000元，需手机动态码验证；验证要求不会跨日降级。")
                        from app.auth_ui import transaction_code
                        code=transaction_code(conversation,key='transfer_totp')
                        if st.button("模拟验证并继续" if conversation.gateway.demo_mode else "验证并继续", type="primary", width="stretch"):
                            perform(conversation.decide, True, simulate_verification=conversation.gateway.demo_mode,totp_code=code)
                    elif st.button("确认此笔模拟转账", type="primary", width="stretch"):
                        perform(conversation.decide, True)
                    if st.button("拒绝此操作", width="stretch"):
                        perform(conversation.decide, False)
                else:
                    st.info(text)
                    if result.get("error", {}).get("code") == "AUTH_EXPIRED":
                        st.write("此笔授权已过期，未执行转账。需要转账时请重新发起并确认。")
                    elif result["status"] == "unknown":
                        st.warning("原请求和操作编号已保留，请查询原操作。未查到记录不能证明未扣款，请勿重复发起。")
                if st.button('查询原操作结果'):
                    perform(conversation.recover)
                with st.expander("查看接口调用示例"):
                    st.caption('操作编号：' + request['idempotency_key'])
                    st.write("请求")
                    st.json(request)
                    st.write("返回结果")
                    st.json(result)
            elif request and result:
                st.info(result_text(request,result))
                st.caption('原操作：'+pending_view.action_label(request['action'])+'；此区域只展示转账详情。')
            elif conversation.draft:
                st.write("收款人：" + conversation.draft["payee_query"])
                st.info("补齐信息或选择收款人后，才会生成转账请求。")
            else:
                st.write("等待你的转账需求。")
                st.caption("准备 → 核对 → 确认或验证 → 查看结果")

    with st.expander('用一句话准备其他业务'):
        st.page_link('pages/0_AI银行助手.py', label='打开真实模型对话入口', icon=':material/auto_awesome:')
        st.caption('下方保留离线备用草稿入口；实际模型识别、追问和任务执行请使用上方入口。')
        selection = render_router(blocked=conversation.blocked)
    if selection and not conversation.blocked:
        destinations = {
            'card': 'pages/3_卡片管理.py', 'aa': 'pages/4_AA收款.py',
            'subscription': 'pages/5_订阅管理.py', 'birthday': 'pages/8_生日与商户.py',
            'scheduled_transfer': 'pages/6_定时转账.py', 'products': 'pages/2_产品对比.py',
            'wealth_assess': 'pages/9_理财.py', 'wealth_purchase': 'pages/9_理财.py',
            'wealth_redeem': 'pages/9_理财.py', 'wealth_holdings': 'pages/9_理财.py',
        }
        st.session_state.routing_handoff = selection
        if selection['intent'] == 'bill' and selection['fields'].get('account_id', '').startswith('ACCT-'):
            st.switch_page('pages/1_资料账单.py')
        elif selection['intent'] in destinations:
            st.switch_page(destinations[selection['intent']])
        elif selection['intent'] == 'bill':
            st.session_state.home_next_tab = '账单流水'
            st.rerun()
        elif selection['intent'] == 'balance':
            st.info('账户首页展示当前账户可用余额；草稿中的账户并未因此获得查询权限，请核对账户标识。')
        elif selection['intent'] == 'transfer':
            st.info('请在下方转账聊天中核对并填写收款人和金额。进入流程不会自动付款，仍需原确认或强验证。')


with history_tab:
    st.title('操作记录')
    with st.expander('恢复待处理操作 / 查看历史', expanded=True):
        transfer_operations = [op for op in conversation.operations if op['action'] == 'transfer.create']
        if any(op['action'] != 'transfer.create' for op in conversation.operations):
            st.caption('卡片、AA、订阅和定时登记可在“账户首页”的“待处理操作”或侧栏对应页面恢复。')
        if transfer_operations:
            labels = {'awaiting_confirmation':'待确认','awaiting_verification':'待模拟验证','succeeded':'已完成',
                      'cancelled':'已拒绝','failed':'未完成','blocked':'已锁定','unknown':'待核实','running':'执行中'}
            selected = st.selectbox('选择原操作', transfer_operations,
                format_func=lambda row: f"{labels.get(row['status'], row['status'])} · {row['idempotency_key']}")
            if st.button('恢复选中操作'):
                perform(conversation.recover, action=selected['action'], idempotency_key=selected['idempotency_key'])
            st.caption('展示全部未决操作及最近 20 条已结束操作；恢复不会自动确认。')
        else:
            st.write('暂无历史操作。')

with ledger_tab:
    st.title('账单与流水')
    st.subheader('近 30 天流水')
    try:
        today = datetime.now(timezone(timedelta(hours=8))).date()
        params = dict(account_id='demo_account_a', start_date=(today-timedelta(days=29)).isoformat(), end_date=today.isoformat(), limit=100)
        data = query('transaction.list', params)
        rows = data['transactions']
        while data['next_cursor']:
            data = query('transaction.list', params | dict(cursor=data['next_cursor']))
            rows.extend(data['transactions'])
        recent_rows = rows
        if rows:
            st.dataframe([{'时间（上海）': datetime.fromisoformat(row['occurred_at'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'), '方向': '转出' if row['direction']=='debit' else '转入',
                '金额': money(row['amount_minor']), '流水编号': row['transaction_id']} for row in reversed(rows)], hide_index=True)
        else:
            st.caption('暂无流水。待确认、拒绝或失败不会产生转账流水。')
    except (ContractError, ServiceError):
        st.warning('流水暂不可用，请稍后查询。')

    st.divider()
    st.subheader('账单分析')
    st.caption('查询账本，不改变余额；修改条件或执行其他操作后请重新查询。')
    try:
        accounts = conversation.gateway.get_context(principal_id=conversation.principal_id)['accounts']
    except (ContractError, ServiceError):
        accounts = []
        st.warning('账单账户暂不可用，请稍后重试。')

    if accounts:
        names = {row['account_id']: row['display_name'] for row in accounts}
        account = st.selectbox('账单账户', list(names), format_func=lambda key: f'{names[key]} · {key}', key='bill_account')
        mode = st.radio('统计期间', ['月份', '年份', '日期范围'], horizontal=True, key='bill_mode')
        current = datetime.now(timezone(timedelta(hours=8))).date()
        if mode in {'月份', '年份'}:
            year = st.number_input('账单年份', min_value=1, max_value=9999, value=current.year, step=1, key='bill_year')
            if mode == '月份':
                month = st.selectbox('账单月份', list(range(1, 13)), index=current.month-1, key='bill_month')
                start, end = date(year, month, 1), date(year, month, monthrange(year, month)[1])
            else:
                start, end = date(year, 1, 1), date(year, 12, 31)
        else:
            start = st.date_input('开始日期（上海）', value=current.replace(day=1), min_value=date.min, max_value=date.max, key='bill_start')
            end = st.date_input('结束日期（上海）', value=current, min_value=date.min, max_value=date.max, key='bill_end')
        valid_range = start is not None and end is not None and start <= end
        if not valid_range:
            st.warning('请选择完整日期，且开始日期不能晚于结束日期。')
        # 不缓存成功报告：失败、条件变化、转账或重置重跑后不会把旧报告当成本次结果。
        if st.button('查询账单', key='bill_query', disabled=not valid_range):
            try:
                bill = query('bill.analyze', dict(account_id=account, start_date=start.isoformat(), end_date=end.isoformat()))
            except (ContractError, ServiceError, TimeoutError):
                st.error('账单查询未完成，请稍后重试；本次没有可展示的报告。')
            else:
                st.caption(f'账户：{names[account]} · 上海日期：{start.isoformat()} 至 {end.isoformat()}（含两端）')
                st.metric('消费/订阅毛支出', money(bill['total_expense_minor']))
                st.write(bill['report'])
                st.info('大额单笔提示仅供核实；其余异常规则未启用，空列表不代表没有异常。')
                if bill['anomalies']:
                    st.dataframe([{'流水编号':item['transaction_id'],'核实提示':item['reason']}
                                  for item in bill['anomalies']],hide_index=True)
                if bill['categories']:
                    st.dataframe([{'分类': row['category'], '金额（元）': money(row['amount_minor']),
                                   '金额（分）': row['amount_minor']} for row in bill['categories']], hide_index=True)
                    st.bar_chart([{'分类': row['category'], '金额（分）': row['amount_minor']}
                                  for row in bill['categories']], x='分类', y='金额（分）')
                    st.caption('图表单位为人民币分，与表格使用同一份接口金额；100 分 = 1 元。')
                else:
                    st.info('该期间没有计入毛支出的消费或订阅出账；退款情况见报告。')
    else:
        st.caption('暂无可用于账单查询的账户。')


pending = list(conversation.unresolved)
# 即使服务端索引已有终态，本地unknown仍须用户主动查询；首页如实保留这项待核实。
pending_keys={(op['action'],op['idempotency_key']) for op in pending}
for flow_key in ('conversation','action_flow','model_bank_flow'):
    local_flow=st.session_state.get(flow_key)
    if local_flow is not None and pending_view.local_unresolved(local_flow) and local_flow.request:
        local_key=(local_flow.request['action'],pending_view.operation_key(local_flow.request))
        if local_key not in pending_keys:
            pending.append(dict(action=local_key[0],idempotency_key=local_key[1],
                status='unknown' if local_flow.needs_reconciliation else local_flow.result['status']))
            pending_keys.add(local_key)
action_names = {
    'transfer.create': '转账', 'wealth.purchase': '理财申购',
    'wealth.redeem': '理财赎回', 'merchant.order': '商户订单',
    'subscription.charge': '订阅续费', 'scheduled_transfer.create': '定时转账',
}
pending_labels = {
    'awaiting_confirmation': '等待确认', 'awaiting_verification': '等待模拟验证',
    'running': '正在处理', 'unknown': '结果待核实',
}
kind_labels = {
    'transfer': '转账', 'wealth_purchase': '理财申购', 'wealth_redeem': '理财赎回',
    'subscription': '订阅', 'consumption': '消费', 'refund': '退款',
}
recent_transactions = None if recent_rows is None else tuple(
    home_view.TransactionSummary(
        title=kind_labels.get(row.get('kind'), '账户交易'),
        time_text=datetime.fromisoformat(row['occurred_at'].replace('Z', '+00:00')).astimezone(
            timezone(timedelta(hours=8))).strftime('%m-%d %H:%M'),
        amount_text=('−' if row['direction'] == 'debit' else '+') + money(row['amount_minor']),
        status_text='已入账',
    )
    for row in list(reversed(recent_rows))[:3]
)
with home_area:
    st.title('个人银行首页')
    st.caption('本地模拟项目 · 不连接真实资金')
    # 待办在首页即可处理：确认/拒绝/查询都走原授权与恢复流程，不另建通道。
    pending_view.render_unresolved(
        conversation, key_prefix='home_pending',
        note='处理完成后首页待办与各业务页会按权威结果更新；模型不会代替你确认或验证。')
    guide_action = onboarding.render_guide()
    selected_service = home_view.render_home(home_view.HomeData(
        account=home_view.AccountSummary(
            name='日常账户', balance_text=money(balance['available_minor']),
            reserved_text=money(balance['reserved_minor']),
        ) if balance else None,
        pending_count=len(pending) if operations_available else None,
        todo_items=tuple(
            action_names.get(op['action'], '业务') + '：' + pending_labels[op['status']]
            for op in pending[:3]
        ) if operations_available else None,
        transactions=recent_transactions,
    ), show_header=False)
    selected_service = guide_action or selected_service
if selected_service == 'home':
    st.session_state.home_next_tab = '账户首页'
    st.rerun()
if selected_service in {'transfer', 'bills'}:
    st.session_state.home_next_tab = '转账汇款' if selected_service == 'transfer' else '账单流水'
    st.rerun()
service_pages = {
    'assistant': 'pages/0_AI银行助手.py',
    'cards': 'pages/3_卡片管理.py', 'aa': 'pages/4_AA收款.py',
    'scheduled': 'pages/6_定时转账.py', 'budget': 'pages/7_预算管理.py',
    'wealth': 'pages/9_理财.py', 'products': 'pages/2_产品对比.py',
}
if selected_service in service_pages:
    st.switch_page(service_pages[selected_service])
