'''银行助手的纯展示组件：只渲染调用方传入的数据，不执行业务动作。'''
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Mapping

import streamlit as st

# 柔彩视觉系统的共用语义色，与 app/visual.py 的静态样式保持一致。
PALETTE = {
    'ink': '#1F3336',
    'ink_strong': '#17302F',
    'paper': '#FBFDFB',
    'surface': '#FFFFFF',
    'muted': '#54686B',
    'border': '#DCE8E4',
    'primary': '#21605F',
    'danger': '#A32A35',
}

THEME_TOKENS = {
    'primaryColor': PALETTE['primary'],
    'backgroundColor': PALETTE['paper'],
    'secondaryBackgroundColor': PALETTE['surface'],
    'textColor': PALETTE['ink'],
    'borderColor': PALETTE['border'],
    'baseRadius': '0.75rem',
    'buttonRadius': '0.75rem',
    'showWidgetBorder': True,
    'headingFontSizes': ['2.15rem', '1.5rem', '1.25rem', '1.125rem', '1rem', '0.875rem'],
}

SIDEBAR_THEME_TOKENS = {
    'backgroundColor': '#FFFFFF',
    'secondaryBackgroundColor': '#F5FAF8',
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
    'render_records',
)

NAV_ITEMS = (
    ('home', 'app/main.py'),
    ('mine', 'app/pages/10_我的.py'),
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
    'home': '首页', 'mine': '我的',
    'accounts': '历史样例分析',
    'products': '产品对比',
    'cards': '卡片管理',
    'aa': 'AA 收款',
    'subscriptions': '订阅管理',
    'scheduled': '预约转账',
    'budget': '预算管理',
    'merchant': '生日计划',
    'wealth': '理财',
    'assistant': 'AI 银行助手',
    'transfer': '转账与收款',
    'bills': '收支明细',
}

PRIMARY_NAV_KEYS = ('home',)

STATUS_META = {
    'draft': {'label': '草稿', 'color': 'gray'},
    'ready': {'label': '可核对', 'color': 'blue'},
    'pending': {'label': '待确认', 'color': 'orange'},
    'running': {'label': '处理中', 'color': 'blue'},
    'blocked': {'label': '受阻', 'color': 'red'},
    'failed': {'label': '未成功', 'color': 'red'},
    'done': {'label': '已完成', 'color': 'green'},
    'unknown': {'label': '结果待核实', 'color': 'gray'},
}

_NAV_LOOKUP = {
    key: (NAV_LABELS.get(key, key), path)
    for key, path in NAV_ITEMS
}
_UNKNOWN_STATUS = {'label': '结果待核实', 'color': 'gray'}
_HOME_VIEWS = {'home': 'home', 'transfer': 'transfer', 'bills': 'bills'}
_NAV_ICONS = {
    'mine': ':material/person:', 'home': ':material/home:', 'assistant': ':material/auto_awesome:',
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
        'awaiting_verification': {'label': '待身份验证', 'color': 'orange'},
        'succeeded': {'label': '已完成', 'color': 'green'},
        'cancelled': {'label': '已取消', 'color': 'gray'},
        'needs_reconciliation': {'label': '结果待核实', 'color': 'orange'},
    }
    meta = STATUS_META.get(key) or business_labels.get(key)
    if meta is None:
        return _UNKNOWN_STATUS, True
    return meta, False


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


def render_service_link(key, label, *, widget_key):
    """Native task link; rendering works for both entrypoint and isolated pages."""
    if st.button(label, key=widget_key):
        _navigate(key, _NAV_LOOKUP[key][1])


def render_sidebar_nav(current_key, *, hide_home_views=False):
    """Native navigation for all pages, with no dependency on a page registry at render time."""
    # The workspace tabs are children of Home when their separate links are hidden.
    active_key = 'home' if hide_home_views and current_key in _HOME_VIEWS else current_key
    groups = (
        ('其他服务', ('cards', 'accounts')),
        ('转账服务', ('aa', 'scheduled')),
        ('生活管理', ('subscriptions', 'budget', 'merchant')),
        ('理财服务', ('products',)),
    )
    def item(key):
        label, path = _NAV_LOOKUP[key]
        clicked = st.button(label, key='ui_menu_' + key, width='stretch',
                            icon=_NAV_ICONS[key],
                            type='primary' if key == active_key else 'tertiary')
        if clicked and key != current_key:
            _navigate(key, path)

    with st.sidebar:
        st.subheader('银行助手')
        st.caption('本机账户')
        with st.container(gap='xsmall'):
            for key in ('home', 'transfer', 'bills', 'wealth', 'mine', 'assistant'):
                if hide_home_views and key in {'transfer','bills'}:
                    continue
                item(key)
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
    with st.container(horizontal=True):
        for key, label, path in selected:
            active = key == current_key
            clicked = st.button(
                label,
                key=f'{key_prefix}_{key}',
                icon=':material/arrow_back:' if key == 'home' else None,
                width='content',
                disabled=active,
                type='tertiary',
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


def render_records(records, *, title_field=None, value_field=None, meta_fields=(),
                   limit=None, key=None, details_label='对账明细', layout='tiles',
                   tone='core', table_view=False, progress_values=None, group_field=None,
                   **table_kwargs):
    '''Render business objects as tiles, activity or steps, without executing them.

    All supplied values remain directly visible by default. Only explicit
    comparison/reconciliation views also expose the original native table.
    '''
    rows = list(records)
    shown = rows if limit is None else rows[:limit]
    tone = tone if tone in {'core', 'ai', 'bill'} else 'core'
    layout = layout if layout in {'tiles', 'bankcards', 'activity', 'steps'} else 'tiles'

    def text(value):
        return '未提供' if _is_blank(value) else str(value)

    def render_blocks():
        if not shown:
            st.caption('暂无记录。')
            return
        tiled = layout in {'tiles', 'bankcards'}
        columns = (None if tiled else
                   [st.container(border=True, key=f'vx-activity-list-{tone}-{key}', gap='xsmall')])
        last_group = None
        for index, row in enumerate(shown):
            if tiled and index % 2 == 0:
                columns = st.columns(min(2, len(shown) - index), gap='large')
            heading_field = title_field or next(iter(row), None)
            fields = meta_fields or tuple(k for k in row if k not in (heading_field, value_field))
            family = ('bankcard' if layout == 'bankcards' else 'tile' if tiled else
                      'step' if layout == 'steps' else 'activity')
            surface_key = f'vx-{family}-{tone}-{key}-{index}' if key else None
            with columns[index % len(columns)]:
                if group_field:
                    group = text(row.get(group_field)).split(' ')[0]
                    if group != last_group:
                        st.caption(group)
                        last_group = group
                with st.container(border=tiled, key=surface_key, gap='small'):
                    if layout == 'bankcards':
                        st.caption(':material/credit_card: 银行卡')
                    if layout == 'steps':
                        st.badge(f'步骤 {index + 1}', color='violet')
                    with st.container(horizontal=not tiled, horizontal_alignment='distribute', gap='small'):
                        with st.container(width='stretch', gap='xsmall'):
                            st.write(text(row.get(heading_field)))
                            if tiled and value_field is not None:
                                st.metric(value_field, text(row.get(value_field)))
                            for field in fields:
                                if field == group_field:
                                    continue
                                if field == '状态':
                                    state = text(row.get(field))
                                    color = ('green' if state in {'正常', '有效', '已完成'} else
                                             'red' if state in {'已冻结', '已挂失', '已停止', '未成功'} else
                                             'orange' if any(word in state for word in ('待', '核实', '尚未')) else 'gray')
                                    st.badge(state, color=color)
                                else:
                                    st.caption(f'{field}：{text(row.get(field))}')
                            if progress_values is not None and index < len(progress_values):
                                value = progress_values[index]
                                if isinstance(value, (int, float)) and 0 <= value <= 1:
                                    st.progress(value, text='剩余预留比例')
                                else:
                                    st.caption('剩余比例暂不可用。')
                        if not tiled and value_field is not None:
                            with st.container(width='content', gap='xsmall'):
                                st.subheader(text(row.get(value_field)))
                                st.caption(value_field)
        if len(shown) < len(rows):
            st.caption(f'共 {len(rows)} 条，当前摘要展示前 {len(shown)} 条。')

    if table_view:
        overview, detail = st.tabs(('流水清单' if layout == 'activity' else '概览', details_label))
        with overview:
            render_blocks()
        with detail:
            return st.dataframe(rows, **table_kwargs)
    else:
        render_blocks()


def render_status(state, *, title=None, detail=None):
    if not _is_blank(title):
        st.subheader(title)
    meta, needs_warning = _status_meta(state)
    st.caption('执行状态')
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
def business_section(title, *, description=None, key=None):
    # key 只用于由 app/visual.py 定义的静态视觉表面（例如 vx-ai-card-chat）；
    # 传 None 时保持原有原生容器行为，控件键与业务顺序都不变。
    with st.container(border=True, key=key) as section:
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
