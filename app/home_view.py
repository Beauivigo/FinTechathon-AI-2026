'''Display-only home page for the simulated bank.

The module renders caller-supplied display values only. It has no access to
session state, storage, bank services, authentication, or page navigation.
A clicked service button is returned to the caller as an action id.
'''

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import streamlit as st


@dataclass(frozen=True)
class AccountSummary:
    '''Display values for one simulated account.'''

    name: Optional[str] = None
    balance_text: Optional[str] = None
    reserved_text: Optional[str] = None


@dataclass(frozen=True)
class TransactionSummary:
    '''Display values for one recent transaction.'''

    title: Optional[str] = None
    time_text: Optional[str] = None
    amount_text: Optional[str] = None
    status_text: Optional[str] = None


@dataclass(frozen=True)
class HomeData:
    '''Values supplied by the caller for one home page render.

    A ``None`` collection means the caller could not provide data. An empty
    sequence means the caller successfully found no records.
    '''

    account: Optional[AccountSummary] = None
    pending_count: Optional[int] = None
    todo_items: Optional[tuple[str, ...]] = None
    transactions: Optional[tuple[TransactionSummary, ...]] = None
    notice: Optional[str] = None


@dataclass(frozen=True)
class ServiceAction:
    '''One service button and the selection id it reports.'''

    action_id: str
    label: str


SERVICE_ACTIONS = (
    ServiceAction('transfer', '转账'),
    ServiceAction('bills', '收支明细'),
    ServiceAction('cards', '卡片管理'),
    ServiceAction('aa', 'AA收款'),
    ServiceAction('scheduled', '预约转账'),
    ServiceAction('budget', '预算管理'),
    ServiceAction('wealth', '理财'),
    ServiceAction('products', '产品对比'),
    ServiceAction('subscriptions', '订阅管理'),
    ServiceAction('merchant', '生日计划'),
    ServiceAction('mine', '我的'),
)
# Each service button's widget key is generated as home_service_<action_id>
# (for example home_service_transfer); the literal key set is unchanged.
_MOCK_NOTICE = '本地模拟项目，不连接真实资金。'
_SERVICE_COLUMNS = 4
# 固定的视觉表面 key（由 app/visual.py 的静态样式识别）。这里只写定常量，
# 不按 DOM 顺序或生成类名区分模块，也不改变任何控件键。
_ACCOUNT_CARD_KEY = 'vx-core-card-home-account'
_ASSISTANT_CARD_KEY = 'vx-ai-card-home-entry'
_SERVICES_CARD_KEY = 'vx-core-card-home-services'
_PENDING_CARD_KEY = 'vx-core-card-home-pending'
_RECENT_CARD_KEY = 'vx-bill-card-home-recent'


def _shown(value: object, fallback: str) -> str:
    '''Return supplied text when usable, otherwise an explicit fallback.'''
    if value is None:
        return fallback
    shown = str(value).strip()
    return shown or fallback


def _render_account(account: Optional[AccountSummary]) -> None:
    if account is None:
        st.write('账户信息暂不可用。')
        return

    st.write(_shown(account.name, '账户名称暂不可用'))
    st.metric('可用余额', _shown(account.balance_text, '余额暂不可用'),
              help='当前可用余额，已扣除预留金额。')
    with st.container(horizontal=True, gap='small'):
        st.caption('预留金额')
        st.write(_shown(account.reserved_text, '预留金额暂不可用'))


def _render_assistant() -> bool:
    st.header('AI 银行助手', icon=':material/auto_awesome:')
    st.write('说出你想办的事，助手会帮你补齐信息、核对步骤。')
    st.caption('试着问：给李明转 200 元，或者帮我查看订阅。')
    clicked = st.button('打开 AI 银行助手', key='home_assistant',
                        icon=':material/chat:', width='stretch', type='primary')
    st.caption('可连接模型辅助；关键操作仍需你确认。')
    return clicked


def _render_services(*, more: bool = False) -> Optional[str]:
    selected = None
    actions = SERVICE_ACTIONS[4:] if more else SERVICE_ACTIONS[:4]
    section = st.expander('全部服务', expanded=False) if more else st.container()
    with section:
        for start in range(0, len(actions), _SERVICE_COLUMNS):
            for column, action in zip(st.columns(_SERVICE_COLUMNS), actions[start:start+_SERVICE_COLUMNS]):
                with column:
                    if st.button(action.label, key='home_service_'+action.action_id, width='stretch'):
                        selected = action.action_id
    return selected


def _render_next(data: Optional[HomeData]) -> None:
    st.header('待处理事项')
    pending_count = None if data is None else data.pending_count
    if pending_count is None:
        st.write('待处理数量暂不可用。')
    elif pending_count == 0:
        st.caption('当前没有待处理事项。')
    else:
        st.warning(f'有 {pending_count} 项业务待处理。')
    todo_items = None if data is None else data.todo_items
    if todo_items is None:
        st.write('待办内容暂不可用。')
    elif todo_items:
        for item in todo_items:
            st.write(_shown(item, '待办内容暂不可用'))
    if pending_count != 0:
        st.caption('可直接在本页“待处理操作”中恢复并确认、拒绝或查询；结果未知或待核实时必须先查询原操作。')


def _transaction_values(transaction: Optional[TransactionSummary]) -> tuple[str, str, str, str]:
    '''Return title, time, amount and status text with explicit fallbacks.'''
    if transaction is None:
        return ('交易内容暂不可用', '时间暂不可用', '金额暂不可用', '状态未知')
    return (
        _shown(transaction.title, '交易内容暂不可用'),
        _shown(transaction.time_text, '时间暂不可用'),
        _shown(transaction.amount_text, '金额暂不可用'),
        _shown(transaction.status_text, '状态未知'),
    )


def _render_transactions(data: Optional[HomeData]) -> None:
    st.header('近期交易')
    transactions = None if data is None else data.transactions
    if transactions is None:
        st.write('近期交易暂不可用。')
        return
    if not transactions:
        st.write('暂无近期交易。')
        return

    # 三条以内用清单式两列展示，避免首页出现密集表格；金额与状态各自独立成行，
    # 窄屏时自然堆叠，长金额不截断。
    for transaction in transactions:
        title, time_text, amount_text, status_text = _transaction_values(transaction)
        detail, figure = st.columns([2.4, 1], gap='small')
        with detail:
            st.write(title)
            st.caption(time_text)
        with figure:
            st.write(amount_text)
            st.caption(status_text)


def _render_usage() -> None:
    with st.expander('使用说明'):
        st.write(
            '先看账户概览，再从常用业务选择要办理的事项。'
            '需要时可直接在首页打开 AI 助手，用自然语言说明需求。'
        )
        st.write(
            '没有取到数据时显示暂不可用；查询完成但没有记录时显示空状态，两者不会混用。'
        )


def render_home(data: Optional[HomeData] = None, *, show_header: bool = True) -> Optional[str]:
    '''Render the home page and return the clicked service action id.

    Args:
        data: Display values supplied by the caller. ``None`` shows an explicit
            unavailable state for the account, pending, todo and transaction
            sections.

    Returns:
        The action id of a clicked service button, or ``None`` when no service
        was clicked. No business operation is executed by this module.
    '''
    if show_header:
        st.title('首页')
        st.caption(_MOCK_NOTICE)

    if data is not None:
        notice = _shown(data.notice, '')
        if notice:
            st.info(notice)

    # First screen: the account facts and the natural-language entry.
    account_area, assistant_area = st.columns([1.4, 1], gap='large')
    with account_area, st.container(border=True, height='stretch', key=_ACCOUNT_CARD_KEY):
        st.header('账户概览', icon=':material/account_balance_wallet:')
        _render_account(None if data is None else data.account)
    with assistant_area, st.container(border=True, height='stretch', key=_ASSISTANT_CARD_KEY):
        assistant_clicked = _render_assistant()

    st.header('常用业务')
    with st.container(border=True, key=_SERVICES_CARD_KEY, gap='xsmall'):
        selected = _render_services()
        more_selected = _render_services(more=True)
    selected = more_selected or selected
    if assistant_clicked:
        selected = 'assistant'

    pending_area, transactions_area = st.columns([1, 1.4], gap='large')
    with pending_area, st.container(border=True, key=_PENDING_CARD_KEY):
        _render_next(data)
    with transactions_area, st.container(border=True, key=_RECENT_CARD_KEY):
        _render_transactions(data)
    _render_usage()

    return selected
