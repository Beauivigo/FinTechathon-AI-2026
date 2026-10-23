'''银行助手的纯展示组件：只渲染调用方传入的数据，不执行业务动作。'''
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Mapping

import streamlit as st

PALETTE = {
    'ink': '#020617',
    'navy': '#0F172A',
    'paper': '#F8FAFC',
    'surface': '#FFFFFF',
    'muted': '#475569',
    'border': '#E2E8F0',
    'brass': '#A16207',
    'danger': '#B91C1C',
}

THEME_TOKENS = {
    'primaryColor': PALETTE['brass'],
    'backgroundColor': PALETTE['paper'],
    'secondaryBackgroundColor': PALETTE['surface'],
    'textColor': PALETTE['ink'],
    'borderColor': PALETTE['border'],
    'baseRadius': '0.5rem',
    'buttonRadius': '0.5rem',
    'showWidgetBorder': True,
    'headingFontSizes': ['2rem', '1.5rem', '1.25rem', '1.125rem', '1rem', '0.875rem'],
}

SIDEBAR_THEME_TOKENS = {
    'backgroundColor': '#FFFFFF',
    'secondaryBackgroundColor': '#F1F5F9',
}

FONT_STACK = (
    'IBM Plex Sans, Noto Sans SC, Source Han Sans SC, '
    'PingFang SC, Microsoft YaHei, sans-serif'
)
SCOPE_NOTE = '这里只展示与核对草稿；确认、权限与强验证仍由原业务流程执行，不会自动付款或执行取消。'

__all__ = (
    'PALETTE',
    'THEME_TOKENS',
    'SIDEBAR_THEME_TOKENS',
    'FONT_STACK',
    'SCOPE_NOTE',
    'NAV_ITEMS',
    'NAV_LABELS',
    'PRIMARY_NAV_KEYS',
    'STATUS_META',
    'render_page_header',
    'render_app_nav',
    'render_overview',
    'render_details',
    'render_status',
    'render_progress',
    'render_empty_state',
    'business_section',
    'render_scope_note',
)

NAV_ITEMS = (
    ('home', 'app/main.py'),
    ('accounts', 'app/pages/1_资料账单.py'),
    ('products', 'app/pages/2_产品对比.py'),
    ('cards', 'app/pages/3_卡片管理.py'),
    ('aa', 'app/pages/4_AA收款.py'),
    ('subscriptions', 'app/pages/5_订阅管理.py'),
    ('scheduled', 'app/pages/6_定时转账.py'),
    ('budget', 'app/pages/7_预算管理.py'),
    ('merchant', 'app/pages/8_生日与商户.py'),
    ('wealth', 'app/pages/9_理财.py'),
    ('assistant', 'app/pages/0_AI银行助手.py'),
    ('transfer', 'app/main.py'),
    ('bills', 'app/main.py'),
)

NAV_LABELS = {
    'home': '银行首页',
    'accounts': '资料账单',
    'products': '产品对比',
    'cards': '卡片管理',
    'aa': 'AA 收款',
    'subscriptions': '订阅管理',
    'scheduled': '定时转账',
    'budget': '预算管理',
    'merchant': '生日与商户',
    'wealth': '理财',
    'assistant': 'AI 银行助手',
    'transfer': '转账汇款',
    'bills': '账单流水',
}

PRIMARY_NAV_KEYS = ('home', 'accounts', 'wealth')

STATUS_META = {
    'draft': {'label': '草稿', 'color': 'gray'},
    'ready': {'label': '可核对', 'color': 'blue'},
    'pending': {'label': '待确认', 'color': 'orange'},
    'running': {'label': '进行中', 'color': 'blue'},
    'blocked': {'label': '受阻', 'color': 'red'},
    'failed': {'label': '未成功', 'color': 'red'},
    'done': {'label': '已完成', 'color': 'green'},
    'unknown': {'label': '状态未知', 'color': 'gray'},
}

_NAV_LOOKUP = {
    key: (NAV_LABELS.get(key, key), path)
    for key, path in NAV_ITEMS
}
_UNKNOWN_STATUS = {'label': '状态未知', 'color': 'gray'}
_HOME_VIEWS = {'home': 'home', 'transfer': 'transfer', 'bills': 'bills'}
_NAV_ICONS = {
    'home': ':material/home:', 'assistant': ':material/auto_awesome:',
    'accounts': ':material/folder_open:', 'bills': ':material/receipt_long:',
    'cards': ':material/credit_card:', 'transfer': ':material/swap_horiz:',
    'aa': ':material/group:', 'scheduled': ':material/schedule:',
    'subscriptions': ':material/subscriptions:', 'budget': ':material/account_balance_wallet:',
    'merchant': ':material/storefront:', 'wealth': ':material/savings:',
    'products': ':material/compare_arrows:',
}


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _entries(items: Any):
    if items is None or isinstance(items, (str, bytes)):
        return []
    if isinstance(items, Mapping):
        return list(items.items())
    try:
        return list(items)
    except TypeError:
        return []


def _entry_parts(entry: Any):
    if isinstance(entry, Mapping):
        return entry.get('label'), entry.get('value'), entry.get('help')
    if isinstance(entry, (tuple, list)) and len(entry) >= 2:
        help_text = entry[2] if len(entry) > 2 else None
        return entry[0], entry[1], help_text
    return None


def _status_meta(state: Any):
    key = state.strip().lower() if isinstance(state, str) else ''
    business_labels = {
        'awaiting_confirmation': {'label': '待确认', 'color': 'orange'},
        'awaiting_verification': {'label': '待模拟验证', 'color': 'orange'},
        'succeeded': {'label': '已完成', 'color': 'green'},
        'cancelled': {'label': '已拒绝', 'color': 'gray'},
        'needs_reconciliation': {'label': '待核实', 'color': 'orange'},
    }
    meta = STATUS_META.get(key) or business_labels.get(key)
    if meta is None:
        return _UNKNOWN_STATUS, True
    return meta, key == 'unknown'


def render_page_header(title, subtitle=None):
    heading = title if not _is_blank(title) else '银行助手'
    st.title(heading)
    if not _is_blank(subtitle):
        st.caption(subtitle)


def _navigate(key, path):
    if key in _HOME_VIEWS:
        st.switch_page(path.removeprefix('app/'), query_params={'view': _HOME_VIEWS[key]})
    else:
        st.switch_page(path.removeprefix('app/'))


def render_sidebar_nav(current_key):
    """Native navigation for all pages, with no dependency on a page registry at render time."""
    groups = (
        ('账户与账单', ('bills', 'cards', 'accounts')),
        ('转账与收款', ('transfer', 'aa', 'scheduled')),
        ('生活管理', ('subscriptions', 'budget', 'merchant')),
        ('理财与产品', ('wealth', 'products')),
    )
    def item(key):
        label, path = _NAV_LOOKUP[key]
        clicked = st.button(label, key='ui_menu_' + key, width='stretch',
                            icon=_NAV_ICONS[key],
                            type='primary' if key == current_key else 'tertiary')
        if clicked and key != current_key:
            _navigate(key, path)

    with st.sidebar:
        st.caption('FinTechathon 2026')
        st.subheader('银行助手')
        st.caption('模拟用户 A · 本地演示')
        with st.container(gap='xsmall'):
            item('home')
            item('assistant')
            for heading, keys in groups:
                with st.expander(heading, expanded=current_key in keys):
                    for key in keys:
                        item(key)


def render_app_nav(current_key, *, keys=PRIMARY_NAV_KEYS, key_prefix='app_nav'):
    if isinstance(keys, str):
        requested = (keys,)
    else:
        try:
            requested = tuple(keys)
        except TypeError:
            requested = PRIMARY_NAV_KEYS
    selected = []
    for key in requested:
        if len(selected) >= 3:
            break
        if not isinstance(key, str):
            continue
        item = _NAV_LOOKUP.get(key)
        if item is None:
            continue
        if any(existing[0] == key for existing in selected):
            continue
        selected.append((key, item[0], item[1]))
    if not selected:
        st.caption('暂无可用导航项。')
        return
    grid = st.columns(len(selected))
    for column, (key, label, path) in zip(grid, selected):
        active = key == current_key
        with column:
            clicked = st.button(
                label,
                key=f'{key_prefix}_{key}',
                width='stretch',
                disabled=active,
                type='primary' if active else 'secondary',
            )
            if clicked and not active:
                _navigate(key, path)


def render_overview(items, *, columns=3, title=None):
    if not _is_blank(title):
        st.subheader(title)
    records = []
    for entry in _entries(items):
        parts = _entry_parts(entry)
        if parts is not None:
            records.append(parts)
    if not records:
        st.caption('暂无可展示的概要数据。')
        return
    try:
        requested = int(columns)
    except (TypeError, ValueError):
        requested = 3
    column_count = max(1, min(3, requested, len(records)))
    grid = st.columns(column_count)
    for position, (label, value, help_text) in enumerate(records):
        metric_args = {
            'label': '—' if _is_blank(label) else str(label),
            'value': '—' if _is_blank(value) else value,
        }
        if not _is_blank(help_text):
            metric_args['help'] = str(help_text)
        with grid[position % column_count]:
            st.metric(**metric_args, border=True)


def render_details(items, *, title=None):
    if not _is_blank(title):
        st.subheader(title)
    records = []
    for entry in _entries(items):
        parts = _entry_parts(entry)
        if parts is not None:
            records.append(parts)
    if not records:
        st.caption('暂无详细信息。')
        return
    for label, value, _help in records:
        if not _is_blank(label):
            st.caption(str(label))
        st.write('未填写' if _is_blank(value) else str(value))


def render_status(state, *, title=None, detail=None):
    if not _is_blank(title):
        st.subheader(title)
    meta, needs_warning = _status_meta(state)
    st.badge(meta['label'], color=meta['color'])
    if not _is_blank(detail):
        st.caption(str(detail))
    if needs_warning:
        st.warning('状态未识别，请按原业务流程核对。')


def render_progress(steps, *, title=None):
    records = []
    for entry in _entries(steps):
        if isinstance(entry, Mapping):
            label = entry.get('label')
            state = entry.get('status', entry.get('state'))
        elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
            label, state = entry[0], entry[1]
        else:
            continue
        records.append((label, state))
    if not records:
        st.caption('暂无进度数据。')
        return
    if not _is_blank(title):
        st.subheader(title)
    for label, state in records:
        if not _is_blank(label):
            st.caption(str(label))
        meta, _needs_warning = _status_meta(state)
        st.badge(meta['label'], color=meta['color'])


def render_empty_state(title, *, description=None, action_label=None, action_key=None):
    with st.container(border=True):
        st.subheader(title if not _is_blank(title) else '暂无数据')
        if not _is_blank(description):
            st.caption(str(description))
        if not _is_blank(action_label) and not _is_blank(action_key):
            return bool(st.button(action_label, key=action_key, width='stretch'))
    return False


@contextmanager
def business_section(title, *, description=None):
    with st.container(border=True) as section:
        if not _is_blank(title):
            st.subheader(title)
        if not _is_blank(description):
            st.caption(str(description))
        yield section


def render_scope_note(extra=None):
    note = SCOPE_NOTE
    if not _is_blank(extra):
        note = f'{SCOPE_NOTE} {extra}'
    st.caption(note)
