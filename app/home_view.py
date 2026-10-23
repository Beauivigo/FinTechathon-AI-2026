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
    ServiceAction('bills', '账单与流水'),
    ServiceAction('cards', '卡片管理'),
    ServiceAction('aa', 'AA收款'),
    ServiceAction('scheduled', '定时转账'),
    ServiceAction('budget', '预算管理'),
    ServiceAction('wealth', '理财'),
    ServiceAction('products', '产品对比'),
)

_MOCK_NOTICE = '本地模拟银行，不连接真实银行或真实资金。'
_SERVICE_COLUMNS = 4


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
              help='当前可以使用的模拟余额，已扣除预留金额。')
    with st.container(horizontal=True, gap='small'):
        st.caption('预留金额')
        st.write(_shown(account.reserved_text, '预留金额暂不可用'))


def _render_assistant() -> bool:
    st.header('AI 银行助手', icon=':material/auto_awesome:')
    st.write('说出你想办的事，助手会帮你补齐信息、核对步骤。')
    st.caption('试着问：给李明转 200 元，或者帮我查看订阅。')
    clicked = st.button('打开 AI 银行助手', key='home_assistant',
                        icon=':material/chat:', width='stretch', type='primary')
    st.caption('首次使用可在助手页面连接模型；涉及操作时仍需你确认。')
    return clicked


def _render_services(*, more: bool = False) -> Optional[str]:
    selected: Optional[str] = None
    for start in (4,) if more else (0,):
        row = SERVICE_ACTIONS[start : start + _SERVICE_COLUMNS]
        # Keep the four frequent actions visible, with management tasks on demand.
        section = st.container() if start == 0 else st.expander('更多服务')
        with section:
            pairs = (row[:2], row[2:])
            for column, pair in zip(st.columns(2, gap='small'), pairs):
                with column:
                    for cell, action in zip(st.columns(2, gap='small', wrap=False), pair):
                        with cell:
                            clicked = st.button(
                                action.label,
                                key=f'home_service_{action.action_id}',
                                width='stretch',
                                icon={
                                    'transfer': ':material/swap_horiz:',
                                    'bills': ':material/receipt_long:',
                                    'cards': ':material/credit_card:',
                                    'aa': ':material/group:',
                                    'scheduled': ':material/schedule:',
                                    'budget': ':material/account_balance_wallet:',
                                    'wealth': ':material/savings:',
                                    'products': ':material/compare_arrows:',
                                }[action.action_id],
                            )
                        if clicked:
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
        st.caption('转账待办可从“操作记录”恢复，其他业务请打开对应服务。结果待核实时，请先查询原操作。')


def _render_transactions(data: Optional[HomeData]) -> None:
    st.header('近期交易')
    transactions = None if data is None else data.transactions
    if transactions is None:
        st.write('近期交易暂不可用。')
        return
    if not transactions:
        st.write('暂无近期交易。')
        return

    table = {
        '时间': [],
        '内容': [],
        '金额': [],
        '状态': [],
    }
    for transaction in transactions:
        if transaction is None:
            table['时间'].append('时间暂不可用')
            table['内容'].append('交易内容暂不可用')
            table['金额'].append('金额暂不可用')
            table['状态'].append('状态未知')
            continue
        table['时间'].append(_shown(transaction.time_text, '时间暂不可用'))
        table['内容'].append(_shown(transaction.title, '交易内容暂不可用'))
        table['金额'].append(_shown(transaction.amount_text, '金额暂不可用'))
        table['状态'].append(_shown(transaction.status_text, '状态未知'))
    st.table(table, alt='近期已入账的模拟交易，含时间、内容、金额与状态。')


def _render_usage() -> None:
    with st.expander('使用说明'):
        st.write(
            '先看账户概览，再从常用业务选择要办理的事项。'
            '转账会打开填写与核对页面，其余入口打开对应服务。'
        )
        st.write(
            '转账等操作继续使用原有的输入、确认和结果恢复流程。'
            '本页显示的余额与交易均为本机模拟数据。'
        )
        st.write(
            '没有取到数据时页面会明确显示暂不可用；查询完成但没有记录时会显示空状态，'
            '两者不会混用。'
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
        st.title('个人银行首页')
        st.caption(_MOCK_NOTICE)

    if data is not None:
        notice = _shown(data.notice, '')
        if notice:
            st.info(notice)

    st.header('常用业务')
    selected = _render_services()

    account_area, assistant_area = st.columns([1.25, 1], gap='medium')
    with account_area, st.container(border=True, height='stretch'):
        st.header('账户概览', icon=':material/account_balance_wallet:')
        _render_account(None if data is None else data.account)
    with assistant_area, st.container(border=True, height='stretch'):
        assistant_clicked = _render_assistant()

    more_selected = _render_services(more=True)
    selected = more_selected or selected
    if assistant_clicked:
        selected = 'assistant'

    pending_area, transactions_area = st.columns([1, 1.4], gap='large')
    with pending_area:
        _render_next(data)
    with transactions_area:
        _render_transactions(data)
    _render_usage()

    return selected
