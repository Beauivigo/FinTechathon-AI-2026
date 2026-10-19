'''Streamlit merchant schedule panel and pure event helpers.

merchant_schedule_panel(params, tasks, now, blocked) only renders the panel and
returns plain event dicts for the host to wire; it never calls bank, agent, or
shared logic:
    {"type": "merchant_schedule_create", "run_at": "<+08:00 ISO>",
     "params": {...}, "idempotency_key": "merchant-schedule:<sha256>"}
    {"type": "merchant_schedule_check"}
    {"type": "merchant_schedule_cancel|restore", "task_id": "..."}
Purchase params keep account_id, reservation_id, item_id, amount_minor (integer
fen), deliver_on (ISO date), delivery_address_id, optional card_id. run_at is
normalized to Beijing +08:00. Draft signature and idempotency key hash the
normalized time plus canonical params, so changing either invalidates the old
confirmation. Each task is read as task_id, state, optional operation_status,
and spec (run_at, payload.params, idempotency_key). dispatched means dispatched,
not paid: tasks do not run while the app is closed and due work must be strongly
revalidated. This panel never executes payments, refunds, or automatic debits.
'''

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping
from typing import Any

import streamlit as st

BEIJING = dt.timezone(dt.timedelta(hours=8))
EVENT_CREATE = 'merchant_schedule_create'
EVENT_CHECK = 'merchant_schedule_check'
EVENT_CANCEL = 'merchant_schedule_cancel'
EVENT_RESTORE = 'merchant_schedule_restore'
TASK_EVENTS = frozenset((EVENT_CHECK, EVENT_CANCEL, EVENT_RESTORE))
_REQUIRED_PARAMS = (
    'account_id',
    'reservation_id',
    'item_id',
    'amount_minor',
    'deliver_on',
    'delivery_address_id',
)


def _as_beijing(run_at: dt.datetime) -> dt.datetime:
    if (
        not isinstance(run_at, dt.datetime)
        or run_at.tzinfo is None
        or run_at.utcoffset() is None
    ):
        raise ValueError('run_at must be a timezone-aware datetime')
    return run_at.astimezone(BEIJING)


def _checked_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(params, Mapping):
        raise ValueError('params must be a purchase params mapping')
    missing = [name for name in _REQUIRED_PARAMS if name not in params]
    if missing:
        raise ValueError('missing params: ' + ', '.join(missing))
    amount = params['amount_minor']
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise ValueError('amount_minor must be integer fen')
    deliver_on = params['deliver_on']
    if not isinstance(deliver_on, str):
        raise ValueError('deliver_on must be an ISO date string')
    try:
        dt.date.fromisoformat(deliver_on)
    except ValueError as exc:
        raise ValueError('deliver_on must be an ISO date') from exc
    for name in ('account_id', 'reservation_id', 'item_id', 'delivery_address_id'):
        if not isinstance(params[name], str) or not params[name].strip():
            raise ValueError(f'{name} must be a non-empty string')
    card_id = params.get('card_id')
    if card_id is not None and (not isinstance(card_id, str) or not card_id.strip()):
        raise ValueError('card_id must be None or a non-empty string')
    return dict(params)


def _canonical(params: Mapping[str, Any] | None, run_at: dt.datetime) -> str:
    checked = _checked_params(params)
    try:
        payload = json.dumps(
            checked, sort_keys=True, separators=(',', ':'), ensure_ascii=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError('params must be JSON-serializable') from exc
    return _as_beijing(run_at).isoformat() + '|' + payload


def draft_signature(params: Mapping[str, Any] | None, run_at: dt.datetime) -> str:
    '''Return the confirmation token for the normalized draft.'''
    return hashlib.sha256(_canonical(params, run_at).encode('utf-8')).hexdigest()


def schedule_idempotency_key(
    params: Mapping[str, Any] | None, run_at: dt.datetime
) -> str:
    '''Return the stable create idempotency key for the canonical draft.'''
    return 'merchant-schedule:' + draft_signature(params, run_at)


def make_create_event(
    params: Mapping[str, Any] | None, run_at: dt.datetime
) -> dict[str, Any]:
    '''Return a create event; copy params and normalize run_at to +08:00.'''
    checked = _checked_params(params)
    at = _as_beijing(run_at)
    return {
        'type': EVENT_CREATE,
        'run_at': at.isoformat(),
        'params': checked,
        'idempotency_key': schedule_idempotency_key(checked, at),
    }


def make_task_event(event_type: str, task_id: str) -> dict[str, str]:
    '''Return a pure check, cancel, or restore event for one task.'''
    if event_type not in TASK_EVENTS:
        raise ValueError('unsupported task event')
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError('task_id must be a non-empty string')
    event = {'type': event_type}
    if event_type != EVENT_CHECK:
        event['task_id'] = task_id
    return event


def make_check_event(task_id: str) -> dict[str, str]:
    '''Return a pure manual-check event; it carries no task_id.'''
    return make_task_event(EVENT_CHECK, task_id)


def make_cancel_event(task_id: str) -> dict[str, str]:
    '''Return a pure cancel event for one task.'''
    return make_task_event(EVENT_CANCEL, task_id)


def make_restore_event(task_id: str) -> dict[str, str]:
    '''Return a pure restore event for one task.'''
    return make_task_event(EVENT_RESTORE, task_id)


def merchant_schedule_panel(
    params: Mapping[str, Any] | None,
    tasks: list[Mapping[str, Any]] | None,
    now: dt.datetime,
    blocked: bool = False,
) -> list[dict[str, Any]]:
    '''Render the registration/task panel and return events from clicked buttons.'''
    events: list[dict[str, Any]] = []
    try:
        now_bj = _as_beijing(now)
    except (TypeError, ValueError):
        now_bj = dt.datetime.now(BEIJING)
    st.subheader('商户定时任务')
    st.caption(f'当前北京时间：{now_bj:%Y-%m-%d %H:%M}')

    p: dict[str, Any] | None = None
    if params is None:
        st.info('当前无可用采购预留，无法登记；已有任务仍可查看、取消或恢复。')
    else:
        try:
            p = _checked_params(params)
        except (TypeError, ValueError) as exc:
            st.error(f'采购参数无效，无法登记：{exc}')
        else:
            st.markdown('**采购信息**')
            st.write(f"账户：{p['account_id']}｜预留：{p['reservation_id']}")
            st.write(f"商品：{p['item_id']}｜金额：{p['amount_minor']} 分")
            st.write(f"配送日：{p['deliver_on']}｜地址：{p['delivery_address_id']}")
            st.write(f"银行卡：{p.get('card_id') or '未提供'}")

    run_at: dt.datetime | None = None
    draft_sig: str | None = None
    if p is not None:
        if 'msp_run_date' not in st.session_state:
            st.session_state['msp_run_date'] = now_bj.date()
        if 'msp_run_time' not in st.session_state:
            st.session_state['msp_run_time'] = now_bj.time().replace(
                second=0, microsecond=0
            )
        run_date = st.date_input('执行日期（北京时间）', key='msp_run_date')
        run_time = st.time_input('执行时间（北京时间）', key='msp_run_time')
        try:
            run_at = dt.datetime.combine(run_date, run_time, tzinfo=BEIJING)
            draft_sig = draft_signature(p, run_at)
        except (TypeError, ValueError) as exc:
            st.error(f'草稿无效，无法登记：{exc}')
            run_at = None
        else:
            st.markdown('**固定草稿摘要**')
            st.write(
                f"账户 {p['account_id']}｜预留 {p['reservation_id']}｜"
                f"商品 {p['item_id']}｜金额 {p['amount_minor']} 分｜"
                f"配送日 {p['deliver_on']}｜地址 {p['delivery_address_id']}｜"
                f"卡 {p.get('card_id') or '未提供'}｜执行 {run_at.isoformat()}"
            )
            st.caption('先确认草稿，再确认登记；日期或采购内容变化后必须重新确认。')

    if blocked or st.session_state.get('msp_confirmed_sig') != draft_sig:
        st.session_state['msp_confirmed_sig'] = None
    st.session_state['msp_draft_sig'] = draft_sig

    can_register = p is not None and draft_sig is not None and not blocked
    if blocked:
        st.warning('登记已暂停（blocked）；已有任务的查看、取消和恢复仍可用。')
    confirm_clicked = st.button(
        '确认草稿', key='msp_confirm', disabled=not can_register
    )
    if confirm_clicked and can_register:
        st.session_state['msp_confirmed_sig'] = draft_sig
    ready = can_register and st.session_state.get('msp_confirmed_sig') == draft_sig
    create_clicked = st.button('确认登记', key='msp_create', disabled=not ready)
    if (
        create_clicked
        and ready
        and not confirm_clicked
        and p is not None
        and run_at is not None
    ):
        events.append(make_create_event(p, run_at))
        st.session_state['msp_confirmed_sig'] = None

    task_list = [
        task
        for task in (tasks or [])
        if isinstance(task, Mapping) and str(task.get('task_id', '')).strip()
    ]
    st.markdown('**已有定时任务**')
    if not task_list:
        st.caption('暂无任务。')
    else:
        for task in task_list:
            spec = task.get('spec') if isinstance(task.get('spec'), Mapping) else {}
            payload = (
                spec.get('payload') if isinstance(spec.get('payload'), Mapping) else {}
            )
            purchase = (
                payload.get('params')
                if isinstance(payload.get('params'), Mapping)
                else {}
            )
            st.write(
                f"任务 {task['task_id']}｜状态 {task.get('state')}｜"
                f"原订单状态 {task.get('operation_status') or '未提供'}｜"
                f"执行 {spec.get('run_at')}｜账户 {purchase.get('account_id')}｜"
                f"预留 {purchase.get('reservation_id')}｜"
                f"商品 {purchase.get('item_id')}｜"
                f"金额 {purchase.get('amount_minor')} 分｜"
                f"配送日 {purchase.get('deliver_on')}｜"
                f"地址 {purchase.get('delivery_address_id')}｜"
                f"卡 {purchase.get('card_id') or '未提供'}｜"
                f"幂等键 {payload.get('idempotency_key') or spec.get('idempotency_key')}"
            )
        task_ids = [str(task['task_id']) for task in task_list]
        selected_id = st.selectbox('选择任务', task_ids, key='msp_task_id')
        selected = next(
            (task for task in task_list if str(task['task_id']) == selected_id), None
        )
        unavailable = selected is None
        if (
            st.button('手动检查', key='msp_check', disabled=unavailable)
            and selected is not None
        ):
            events.append(make_check_event(str(selected['task_id'])))
        if (
            st.button('确认取消', key='msp_cancel', disabled=unavailable)
            and selected is not None
        ):
            events.append(make_cancel_event(str(selected['task_id'])))
        if (
            st.button('恢复任务', key='msp_restore', disabled=unavailable)
            and selected is not None
        ):
            events.append(make_restore_event(str(selected['task_id'])))
    st.caption(
        'dispatched 仅表示已派发，不代表付款；程序关闭时不运行，到期需重新强验证；'
        '不套用转账时间限制；本面板不退款、不自动扣款。'
    )
    return events
