"""启动：python -m streamlit run app/main.py。"""

from pathlib import Path
import sys
from datetime import date, datetime, timedelta, timezone
from calendar import monthrange
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from agent.controller import Conversation
from bank.gateway import BankGateway
from bank.seed import initialize, demo_path, reset_demo
from bank.store import Store
from shared import ContractError, ServiceError, result_text, adapt_request, validate_result


st.set_page_config(page_title="FinTechathon · 转账演示", page_icon="💬", layout="wide")


def new_conversation():
    store = Store(demo_path())
    initialize(store)  # 只在首次导入；再次打开保留业务记录。
    return Conversation(BankGateway(store), principal_id='demo_user_a')


if "conversation" not in st.session_state:
    try:
        st.session_state.conversation = new_conversation()
    except Exception:
        st.error('模拟银行暂时无法打开。请检查数据库，不要删除数据库来绕过核查。')
        st.stop()
conversation = st.session_state.conversation
other_flow = st.session_state.get('action_flow')
conversation.external_blocked = bool(other_flow and other_flow.needs_reconciliation)
if conversation.external_blocked:
    st.warning('其他业务的结果尚待核实，请到对应页面查询原操作。')
try:
    conversation.refresh_operations()
except ServiceError:
    st.warning('暂时无法刷新操作列表，请稍后重试。')


def perform(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except (ValueError, ContractError, ServiceError) as exc:
        # 用户文案不直接暴露后端错误详情；草稿错误给出可操作的输入提示。
        message = str(exc) if type(exc) is ValueError else "调用未完成，请查询原操作；不要重复发起或重置来绕过核查。"
        conversation.messages.append({"role": "assistant", "content": message})
    st.rerun()


def query(action, params):
    request = adapt_request(action, params, request_id='view_'+uuid4().hex, trace_id='page_'+uuid4().hex)
    result = validate_result(request, conversation.gateway.execute(request, principal_id=conversation.principal_id))
    if result['status'] != 'succeeded':
        raise ServiceError(result.get('error', {}).get('code', 'UPSTREAM_ERROR'), '查询暂不可用。')
    return result['data']


def money(amount):
    return f'¥ {amount // 100:,}.{amount % 100:02d}'


with st.sidebar:
    st.title("FinTechathon")
    st.caption("银行助手 · 本地演示")
    st.divider()
    st.subheader("试一笔转账")
    for index, example in enumerate(("给李明转200元", "给小王转钱", "给小王转两百", "给李明转1200元")):
        if st.button(example, key=f"example_{index}", disabled=conversation.blocked, use_container_width=True):
            perform(conversation.submit, example)
    st.caption("小王有两个模拟账户，需要根据尾号选择。")
    st.divider()
    st.write("付款账户")
    st.write("模拟日常账户")
    try:
        balance = query('account.balance', {'account_id': 'demo_account_a'})
        st.metric('可用余额', money(balance['available_minor']))
        st.caption('模拟余额保存在本地，刷新不会重置。')
    except (ContractError, ServiceError):
        st.warning('余额暂不可用。')
    if st.button("新建对话", disabled=conversation.blocked, use_container_width=True):
        st.session_state.conversation = new_conversation()
        st.rerun()
    st.caption('新建对话只清空聊天草稿，不改变余额和流水。')
    with st.expander('重置本地模拟银行'):
        st.write('会清除全部模拟业务、授权和流水，恢复 A 的 2000 元初始余额。')
        confirmed = st.checkbox('我确认清除模拟数据', disabled=conversation.blocked)
        if st.button('确认重置模拟银行', disabled=conversation.blocked or not confirmed):
            try:
                current = conversation.gateway.list_operations(principal_id=conversation.principal_id)
                if any(op['status'] in {'awaiting_confirmation','awaiting_verification','running','unknown'} for op in current):
                    raise ValueError('请先处理或核实全部待办，再重置模拟银行。')
                reset_demo(confirm=True)
                st.session_state.pop('action_flow', None)
                st.session_state.conversation = new_conversation()
                st.rerun()
            except (ValueError, ServiceError):
                st.error('重置未完成。请先处理或核实全部待办。')

st.caption("A 第二轮 · 持久化模拟银行")
st.title("把一笔转账说清楚")
st.write("告诉我收款人和金额。信息不完整时，我会先向你确认。")
st.info("本地模拟银行：确认后会修改模拟余额并保存流水。使用离线中文解析和模拟强验证，不接真实银行或真实资金。")

with st.expander('恢复待处理操作 / 查看历史', expanded=bool(conversation.operations) and not conversation.request):
    transfer_operations = [op for op in conversation.operations if op['action'] == 'transfer.create']
    if any(op['action'] != 'transfer.create' for op in conversation.operations):
        st.caption('卡片、AA、订阅和定时登记请到侧栏对应页面恢复。')
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
if conversation.blocked and not conversation.request:
    st.warning('请先恢复并处理待办；未知结果必须先核实。')

chat, details = st.columns([1.5, 1], gap="large")
with chat:
    st.subheader("对话")
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
            if st.button(label, key="payee_" + candidate["payee_id"], use_container_width=True):
                perform(conversation.select_payee, candidate["payee_id"])
    prompt = st.chat_input("例如：给小王转200元", key="transfer_input", max_chars=200, disabled=conversation.blocked)
    if prompt:
        perform(conversation.submit, prompt)

with details:
    st.subheader("当前操作")
    request, result = conversation.request, conversation.result
    if request and result:
        amount = request["params"]["amount_minor"]
        st.metric("转账金额", f"¥ {amount // 100:,}.{amount % 100:02d}")
        st.write("收款人：" + conversation.payee["display_name"])
        st.write("收款账户：" + conversation.payee["masked_account"])
        st.write("银行：" + conversation.payee.get("bank_name", "未提供"))
        st.divider()
        text = result_text(request, result)
        if result["status"] == "succeeded":
            st.success(text)
            st.caption("该结果来自已提交的本地模拟账本。")
        elif result["status"] in {"awaiting_confirmation", "awaiting_verification"}:
            st.warning(text)
            if result["status"] == "awaiting_verification":
                st.write("含本次的上海当日累计超过1000元，需要模拟强验证；已提出的验证要求不会跨日降级。")
                if st.button("模拟验证并继续", type="primary", use_container_width=True):
                    perform(conversation.decide, True, simulate_verification=True)
            elif st.button("确认此笔模拟转账", type="primary", use_container_width=True):
                perform(conversation.decide, True)
            if st.button("拒绝此操作", use_container_width=True):
                perform(conversation.decide, False)
        else:
            st.info(text)
            if result.get("error", {}).get("code") == "AUTH_EXPIRED":
                st.write("此笔授权已过期，未执行转账。需要转账时请重新发起并确认。")
            elif result["status"] == "unknown":
                st.warning("原请求和操作编号已保留，请查询原操作。未查到记录不能证明未扣款，请勿重复发起。")
        if st.button('查询原操作结果'):
            perform(conversation.recover)
        st.caption('操作编号：' + request['idempotency_key'])
        with st.expander("查看接口调用示例"):
            st.write("请求")
            st.json(request)
            st.write("返回结果")
            st.json(result)
    elif conversation.draft:
        st.write("收款人：" + conversation.draft["payee_query"])
        st.info("补齐信息或选择收款人后，才会生成转账请求。")
    else:
        st.write("等待你的转账需求。")
        st.caption("准备请求 → 核对详情 → 确认或模拟验证 → 查看账本结果")

st.subheader('近 30 天流水')
try:
    today = datetime.now(timezone(timedelta(hours=8))).date()
    params = dict(account_id='demo_account_a', start_date=(today-timedelta(days=29)).isoformat(), end_date=today.isoformat(), limit=100)
    data = query('transaction.list', params)
    rows = data['transactions']
    while data['next_cursor']:
        data = query('transaction.list', params | dict(cursor=data['next_cursor']))
        rows.extend(data['transactions'])
    if rows:
        st.dataframe([{'时间（上海）': datetime.fromisoformat(row['occurred_at'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'), '方向': '转出' if row['direction']=='debit' else '转入',
            '金额': money(row['amount_minor']), '流水编号': row['transaction_id']} for row in reversed(rows)], hide_index=True)
    else:
        st.caption('暂无流水。待确认、拒绝或失败不会产生转账流水。')
except (ContractError, ServiceError):
    st.warning('流水暂不可用，请稍后查询。')

st.divider()
st.subheader('账单分析')
st.caption('查询本地模拟账本，不改变余额。修改查询条件或执行其他操作后，请重新查询。')
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
            st.info('自动异常检测尚未启用，空列表不代表没有异常。')
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
