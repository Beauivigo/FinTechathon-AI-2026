'''冻结测试：仅使用标准库与最小 fake streamlit，不启动真实页面。'''
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_PATH = ROOT / 'app' / 'ui.py'
DOC_PATH = ROOT / 'docs' / 'UI设计与演示.md'


class _FakeContainer:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__('streamlit')
        self.calls = []
        self.button_results = {}
        self.sidebar = _FakeContainer()

    def reset(self):
        self.calls.clear()
        self.button_results.clear()

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == 'button':
                return bool(self.button_results.get(kwargs.get('key'), False))
            if name == 'columns':
                spec = args[0] if args else kwargs.get('spec', 1)
                count = spec if isinstance(spec, int) else len(spec)
                return [_FakeContainer() for _ in range(count)]
            if name == 'tabs':
                return [_FakeContainer() for _ in args[0]]
            if name in {'container', 'expander'}:
                return _FakeContainer()
            return None

        return call


def _load_ui():
    fake = FakeStreamlit()
    previous = sys.modules.get('streamlit')
    sys.modules['streamlit'] = fake
    try:
        spec = importlib.util.spec_from_file_location('ui_under_test', UI_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError('cannot load app/ui.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop('streamlit', None)
        else:
            sys.modules['streamlit'] = previous
    return module, fake


def _luminance(color):
    values = []
    for index in (1, 3, 5):
        channel = int(color[index:index + 2], 16) / 255.0
        if channel <= 0.04045:
            values.append(channel / 12.92)
        else:
            values.append(((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]


def _contrast(first, second):
    high = max(_luminance(first), _luminance(second))
    low = min(_luminance(first), _luminance(second))
    return (high + 0.05) / (low + 0.05)


class UiComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui, cls.st = _load_ui()

    def setUp(self):
        self.st.reset()

    def found(self, name):
        return [(args, kwargs) for call_name, args, kwargs in self.st.calls if call_name == name]

    @staticmethod
    def argument(call, index, name, default=None):
        args, kwargs = call
        if name in kwargs:
            return kwargs[name]
        if index < len(args):
            return args[index]
        return default

    def assert_safe_native_calls(self):
        names = {name for name, _, _ in self.st.calls}
        self.assertNotIn('html', names)
        self.assertNotIn('markdown', names)
        self.assertNotIn('success', names)
        self.assertNotIn('error', names)
        for _, _, kwargs in self.st.calls:
            self.assertNotIn('unsafe_allow_html', kwargs)
            self.assertNotIn('use_container_width', kwargs)

    def test_constants_are_exact_local_and_accessible(self):
        self.assertEqual(self.ui.PALETTE, {
            'ink': '#1F3336',
            'ink_strong': '#17302F',
            'paper': '#FBFDFB',
            'surface': '#FFFFFF',
            'muted': '#54686B',
            'border': '#DCE8E4',
            'primary': '#21605F',
            'danger': '#A32A35',
        })
        self.assertEqual(self.ui.THEME_TOKENS, {
            'primaryColor': '#21605F',
            'backgroundColor': '#FBFDFB',
            'secondaryBackgroundColor': '#FFFFFF',
            'textColor': '#1F3336',
            'borderColor': '#DCE8E4',
            'baseRadius': '0.75rem',
            'buttonRadius': '0.75rem',
            'showWidgetBorder': True,
            'headingFontSizes': ['2.15rem', '1.5rem', '1.25rem', '1.125rem', '1rem', '0.875rem'],
        })
        self.assertEqual(self.ui.SIDEBAR_THEME_TOKENS, {
            'backgroundColor': '#FFFFFF',
            'secondaryBackgroundColor': '#F5FAF8',
        })
        for part in ('IBM Plex Sans', 'Noto Sans SC', 'Source Han Sans SC', 'PingFang SC', 'Microsoft YaHei', 'sans-serif'):
            self.assertIn(part, self.ui.FONT_STACK)
        self.assertNotIn('http', self.ui.FONT_STACK.lower())
        self.assertIn('原业务流程', self.ui.SCOPE_NOTE)
        self.assertIn('不会自动付款', self.ui.SCOPE_NOTE)
        self.assertNotIn('已实现', self.ui.SCOPE_NOTE)
        self.assertEqual(dict(self.ui.NAV_ITEMS), {
            'home': 'app/main.py',
            'mine':'app/pages/10_我的.py',
            'accounts': 'app/pages/1_资料账单.py',
            'products': 'app/pages/2_产品对比.py',
            'cards': 'app/pages/3_卡片管理.py',
            'aa': 'app/pages/4_AA收款.py',
            'subscriptions': 'app/pages/5_订阅管理.py',
            'scheduled': 'app/pages/6_定时转账.py',
            'budget': 'app/pages/7_预算管理.py',
            'merchant': 'app/pages/8_生日与商户.py',
            'wealth': 'app/pages/9_理财.py',
            'assistant': 'app/pages/0_AI银行助手.py',
            'transfer': 'app/main.py',
            'bills': 'app/main.py',
        })
        self.assertEqual(self.ui.NAV_LABELS['home'], '首页')
        self.assertEqual(self.ui.PRIMARY_NAV_KEYS, ('home',))
        self.assertEqual(set(self.ui.STATUS_META), {'draft', 'ready', 'pending', 'running', 'blocked', 'failed', 'done', 'unknown'})
        self.assertEqual(self.ui.STATUS_META['done'], {'label': '已完成', 'color': 'green'})
        self.assertGreaterEqual(_contrast(self.ui.PALETTE['ink'], self.ui.PALETTE['paper']), 7.0)
        self.assertGreaterEqual(_contrast('#FFFFFF', self.ui.PALETTE['primary']), 4.5)
        self.assertGreaterEqual(_contrast(self.ui.PALETTE['muted'], self.ui.PALETTE['surface']), 4.5)
        self.assertGreaterEqual(_contrast(self.ui.PALETTE['danger'], self.ui.PALETTE['surface']), 4.5)

    def test_records_show_all_values_by_default_without_hiding_them_in_tables(self):
        rows = [{'名称': f'记录{i}', '金额': f'{i}.00', '状态': '待核对'} for i in range(5)]
        original = [dict(row) for row in rows]
        self.ui.render_records(rows, title_field='名称', value_field='金额',
                               meta_fields=('状态',), key='records-test', hide_index=True)
        shown = [str(args[0]) for name, args, _kwargs in self.st.calls
                 if name in {'write', 'text', 'caption', 'subheader', 'badge'} and args]
        shown.extend(str(self.argument(call, 1, 'value')) for call in self.found('metric'))
        for row in rows:
            self.assertIn(row['名称'], shown)
            self.assertIn(row['金额'], shown)
        self.assertTrue(any('待核对' in value for value in shown))
        self.assertEqual(rows, original)
        self.assertEqual(self.found('dataframe'), [])
        self.assertEqual(self.found('expander'), [])
        self.assertEqual(self.found('tabs'), [])
        self.assert_safe_native_calls()

    def test_records_keep_zero_missing_and_untrusted_text_as_native_values(self):
        rows = [{'参与人': '<script>value</script>', '金额（元）': '0.00'},
                {'参与人': '乙', '金额（元）': 0}, {'参与人': '丙', '金额（元）': None},
                {'参与人': '丁', '金额（元）': '12.34'}]
        original = [dict(row) for row in rows]
        self.ui.render_records(rows, title_field='参与人', value_field='金额（元）',
                               layout='activity', tone='core', key='all-records', hide_index=True)
        shown = [str(args[0]) for name, args, _kwargs in self.st.calls
                 if name in {'write', 'text', 'caption', 'subheader'} and args]
        for value in ('<script>value</script>', '乙', '丙', '丁', '0.00', '0', '未提供', '12.34'):
            self.assertIn(value, shown)
        self.assertEqual(shown.count('未提供'), 1)
        self.assertEqual(rows, original)
        self.assertEqual(self.found('dataframe'), [])
        self.assert_safe_native_calls()

    def test_explicit_record_table_retains_full_native_data_and_empty_records_stay_empty(self):
        rows = [{'名称': f'记录{i}', '金额': f'{i}.00'} for i in range(5)]
        self.ui.render_records(rows, title_field='名称', value_field='金额',
                               layout='activity', tone='bill', table_view=True, limit=2,
                               key='records-with-table', hide_index=True,
                               column_order=['名称', '金额'])
        self.assertEqual(len(self.found('tabs')), 1)
        self.assertEqual(len(self.argument(self.found('tabs')[0], 0, 'tabs')), 2)
        self.assertEqual(len(self.found('dataframe')), 1)
        table = self.found('dataframe')[0]
        self.assertEqual(self.argument(table, 0, 'data'), rows)
        self.assertTrue(table[1]['hide_index'])
        self.assertEqual(table[1]['column_order'], ['名称', '金额'])
        self.assertEqual(self.found('expander'), [])
        self.assert_safe_native_calls()
        self.st.reset()
        self.ui.render_records([], key='empty-records', hide_index=True)
        self.assertEqual(self.found('subheader'), [])
        self.assertEqual(self.found('write'), [])
        self.assertEqual(self.found('dataframe'), [])
        self.assertEqual(self.found('tabs'), [])
        self.assertIn('暂无记录。', [self.argument(call, 0, 'body') for call in self.found('caption')])
        self.assert_safe_native_calls()

    def test_docs_cover_design_tokens_wiring_and_boundaries(self):
        self.assertTrue(DOC_PATH.is_file(), 'missing UI design document')
        doc = DOC_PATH.read_text(encoding='utf-8')
        for heading in ('## 设计方向', '## 设计 token 与主题配置', '## 组件 API', '## Codex 最终接线', '## 真实能力与边界', '## 本地启动与验证'):
            self.assertIn(heading, doc)
        for api in ('render_page_header', 'render_app_nav', 'business_section', 'render_overview', 'render_details', 'render_status', 'render_progress', 'render_empty_state', 'render_scope_note'):
            self.assertIn(api, doc)
        for key in self.ui.THEME_TOKENS:
            self.assertIn(key, doc)
        for key in ('primaryColor', 'backgroundColor', 'secondaryBackgroundColor', 'textColor', 'borderColor'):
            self.assertIn(self.ui.THEME_TOKENS[key], doc)
        for path in dict(self.ui.NAV_ITEMS).values():
            self.assertIn(path, doc)
        self.assertIn('[theme.sidebar]', doc)
        self.assertIn('current_key', doc)
        self.assertIn('st.columns([1.5,1])', doc)
        self.assertIn('streamlit run app/main.py', doc)
        self.assertIn('python -m unittest discover -s tests -p test_ui_components.py -v', doc)
        self.assertIn('AppTest', doc)
        self.assertIn('Codex', doc)
        self.assertIn('from app import ui', doc)
        self.assertIn('不会自动付款', doc)
        # Plain source citations do not load external page assets. Keep remote
        # images, code/config URLs and unsafe rendering outside that exception.
        doc_without_citations = re.sub(r'(?<!!)\[[^\]]+\]\(https?://[^)\s]+\)', '[reference]', doc)
        for forbidden in ('http://', 'https://', 'fonts.googleapis', 'unsafe_allow_html', '[server]', 'enableStaticServing'):
            self.assertNotIn(forbidden, doc_without_citations)

    def test_import_boundary_and_no_forbidden_native_apis(self):
        tree = ast.parse(UI_PATH.read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        self.assertLessEqual(imported, {'__future__', 'contextlib', 'typing', 'streamlit'})
        forbidden_attributes = {'session_state', 'cache_data', 'cache_resource', 'html', 'markdown', 'success', 'error'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'st':
                self.assertNotIn(node.attr, forbidden_attributes)
        self.ui.render_page_header('<b>标题</b>', '<img src=x onerror=alert(1)>')
        self.ui.render_overview([{'label': '<b>概要</b>', 'value': '<img src=x>'}], title='<i>标题</i>')
        self.ui.render_details({'备注': '<script>alert(1)</script>'}, title='<b>细节</b>')
        self.ui.render_status('mystery', title='<b>状态</b>', detail='<i>未决</i>')
        self.ui.render_empty_state('<b>空</b>', description='<i>说明</i>')
        self.ui.render_scope_note('<u>补充</u>')
        self.assert_safe_native_calls()

    def test_page_header_keeps_untrusted_text_out_of_html(self):
        title = '<b>转账</b>'
        subtitle = '<img src=x onerror=alert(1)>'
        self.ui.render_page_header(title, subtitle)
        titles = self.found('title')
        self.assertEqual(len(titles), 1)
        self.assertEqual(self.argument(titles[0], 0, 'body'), title)
        captions = self.found('caption')
        self.assertEqual(len(captions), 1)
        self.assertEqual(self.argument(captions[0], 0, 'body'), subtitle)
        self.assertEqual(len(self.found('divider')), 0)
        self.assert_safe_native_calls()
        self.st.reset()
        self.ui.render_page_header('', '')
        titles = self.found('title')
        self.assertEqual(len(titles), 1)
        self.assertEqual(self.argument(titles[0], 0, 'body'), '银行助手')
        self.assertEqual(self.found('caption'), [])

    def test_navigation_has_stable_keys_and_only_clicks_switch_pages(self):
        routes = dict(self.ui.NAV_ITEMS)
        self.st.button_results = {'app_nav_wealth': True}
        self.ui.render_app_nav('home', keys=('home','accounts','wealth'))
        buttons = self.found('button')
        self.assertEqual(len(buttons), 3)
        self.assertEqual([self.argument(call, 0, 'label') for call in buttons], ['首页', '历史样例分析', '理财'])
        self.assertEqual([self.argument(call, 0, 'key') for call in buttons], ['app_nav_home', 'app_nav_accounts', 'app_nav_wealth'])
        self.assertIs(self.argument(buttons[0], 0, 'disabled'), True)
        self.assertEqual(self.argument(buttons[0], 0, 'type'), 'tertiary')
        self.assertIs(self.argument(buttons[2], 0, 'disabled'), False)
        self.assertEqual(self.argument(buttons[2], 0, 'type'), 'tertiary')
        for call in buttons:
            self.assertEqual(self.argument(call, 0, 'width'), 'content')
        switches = self.found('switch_page')
        self.assertEqual(len(switches), 1)
        self.assertEqual(self.argument(switches[0], 0, 'page'), routes['wealth'].removeprefix('app/'))
        self.st.reset()
        self.st.button_results = {'app_nav_home': True}
        self.ui.render_app_nav('home', keys=('home','accounts','wealth'))
        self.assertEqual(self.found('switch_page'), [])
        self.assert_safe_native_calls()

    def test_hidden_workspace_children_keep_their_home_parent_highlighted(self):
        for current_key in ('home', 'transfer', 'bills'):
            with self.subTest(current_key=current_key):
                self.st.reset()
                self.ui.render_sidebar_nav(current_key, hide_home_views=True)
                active = [self.argument(call, 0, 'key') for call in self.found('button')
                          if self.argument(call, 0, 'type') == 'primary']
                self.assertEqual(active, ['ui_menu_home'])
                self.assertFalse(any(self.argument(call, 0, 'key') in {'ui_menu_transfer', 'ui_menu_bills'}
                                     for call in self.found('button')))
                self.assertEqual(self.found('switch_page'), [])
        self.st.reset()
        self.ui.render_sidebar_nav('bills')
        active = [self.argument(call, 0, 'key') for call in self.found('button')
                  if self.argument(call, 0, 'type') == 'primary']
        self.assertEqual(active, ['ui_menu_bills'])

    def test_overview_empty_and_none_values_are_neutral(self):
        self.ui.render_overview([])
        self.assertEqual(self.found('metric'), [])
        captions = self.found('caption')
        self.assertTrue(captions)
        self.assertIn('暂无', str(self.argument(captions[0], 0, 'body')))
        self.st.reset()
        self.ui.render_overview([
            {'label': '可用余额', 'value': '后台传入值', 'help': '现有账户数据'},
            {'label': '待确认', 'value': None},
        ])
        metrics = self.found('metric')
        self.assertEqual(len(metrics), 2)
        self.assertEqual(self.argument(metrics[0], 0, 'label'), '可用余额')
        self.assertEqual(self.argument(metrics[0], 1, 'value'), '后台传入值')
        self.assertEqual(self.argument(metrics[1], 0, 'label'), '待确认')
        self.assertEqual(self.argument(metrics[1], 1, 'value'), '—')
        columns = self.found('columns')
        self.assertEqual(len(columns), 1)
        self.assertLessEqual(self.argument(columns[0], 0, 'spec'), 3)
        self.assertEqual(self.found('success'), [])
        self.assert_safe_native_calls()

    def test_details_show_passed_values_and_missing_fields(self):
        self.ui.render_details([
            ('收款人', '<b>张三</b>'),
            {'label': '备注', 'value': None},
            ('金额原文', ''),
        ], title='转账草稿')
        writes = self.found('write')
        self.assertEqual(len(writes), 3)
        values = [self.argument(call, 0, 'body') for call in writes]
        self.assertEqual(values[0], '<b>张三</b>')
        self.assertEqual(values[1], '未填写')
        self.assertEqual(values[2], '未填写')
        self.assertTrue(self.found('subheader'))
        self.assertEqual(self.found('metric'), [])
        self.assert_safe_native_calls()
        self.st.reset()
        self.ui.render_details([])
        self.assertEqual(self.found('write'), [])
        self.assertTrue(self.found('caption'))

    def test_unknown_status_and_progress_never_claim_success(self):
        self.ui.render_status('mystery', title='核对结果', detail='<i>未决</i>')
        badges = self.found('badge')
        self.assertEqual(len(badges), 1)
        self.assertEqual(self.argument(badges[0], 0, 'label'), '结果待核实')
        self.assertEqual(self.argument(badges[0], 1, 'color'), 'gray')
        self.assertEqual(len(self.found('warning')), 1)
        self.assertEqual(self.found('success'), [])
        self.assertEqual(self.found('error'), [])
        self.assert_safe_native_calls()
        self.st.reset()
        self.ui.render_status('done', title='原流程返回完成')
        badges = self.found('badge')
        self.assertEqual(self.argument(badges[0], 0, 'label'), '已完成')
        self.assertEqual(self.argument(badges[0], 1, 'color'), 'green')
        self.assertEqual(self.found('success'), [])
        self.assertEqual(self.found('warning'), [])
        self.st.reset()
        self.ui.render_progress([
            {'label': '创建草稿', 'status': 'done'},
            ('确认执行', 'pending'),
            {'label': '外部结果', 'status': 'mystery'},
        ], title='操作进度')
        badges = self.found('badge')
        self.assertEqual([self.argument(call, 0, 'label') for call in badges], ['已完成', '待确认', '结果待核实'])
        self.assertEqual(self.argument(badges[2], 1, 'color'), 'gray')
        self.assertEqual(self.found('success'), [])
        self.st.reset()
        self.ui.render_progress([])
        self.assertEqual(self.found('badge'), [])
        self.assertTrue(self.found('caption'))

    def test_empty_state_returns_click_and_business_section_preserves_controls(self):
        self.st.button_results = {'empty_refresh': True}
        clicked = self.ui.render_empty_state(
            '暂无草稿',
            description='先通过现有流程创建。',
            action_label='刷新现有数据',
            action_key='empty_refresh',
        )
        self.assertIs(clicked, True)
        buttons = self.found('button')
        self.assertEqual(len(buttons), 1)
        self.assertEqual(self.argument(buttons[0], 0, 'label'), '刷新现有数据')
        self.assertEqual(self.argument(buttons[0], 0, 'key'), 'empty_refresh')
        self.assertEqual(self.argument(buttons[0], 0, 'width'), 'stretch')
        self.assertTrue(self.found('container'))
        self.assertEqual(self.found('success'), [])
        self.assert_safe_native_calls()
        self.st.reset()
        self.assertIs(self.ui.render_empty_state('暂无数据'), False)
        self.assertEqual(self.found('button'), [])
        self.st.reset()
        with self.ui.business_section('转账', description='仅展示已有草稿字段'):
            self.st.write('existing_control')
        containers = self.found('container')
        self.assertEqual(len(containers), 1)
        self.assertIs(self.argument(containers[0], 0, 'border'), True)
        self.assertEqual(len(self.found('subheader')), 1)
        writes = [self.argument(call, 0, 'body') for call in self.found('write')]
        self.assertIn('existing_control', writes)
        # 视觉表面通过显式稳定 key 传入；未传时保持原生容器行为。
        self.st.reset()
        with self.ui.business_section('转账', key='vx-ai-card-chat'):
            self.st.write('existing_control')
        containers = self.found('container')
        self.assertEqual(len(containers), 1)
        self.assertEqual(self.argument(containers[0], 0, 'key'), 'vx-ai-card-chat')
        self.assertIs(self.argument(containers[0], 0, 'border'), True)

    def test_scope_note_reports_true_boundary(self):
        self.ui.render_scope_note()
        captions = self.found('caption')
        self.assertEqual(len(captions), 1)
        text = str(self.argument(captions[0], 0, 'body'))
        self.assertIn('原业务流程', text)
        self.assertIn('不会自动付款', text)
        self.assertNotIn('已实现', text)
        self.assert_safe_native_calls()

    def test_navigation_ignores_unknown_keys_and_empty_requests(self):
        self.ui.render_app_nav('home', keys=('unknown',))
        self.assertEqual(self.found('button'), [])
        self.assertTrue(self.found('caption'))
        self.st.reset()
        self.ui.render_app_nav(None, keys=('home',))
        buttons = self.found('button')
        self.assertEqual(len(buttons), 1)
        self.assertIs(self.argument(buttons[0], 0, 'disabled'), False)
        self.assertEqual(self.argument(buttons[0], 0, 'type'), 'tertiary')
        self.assertEqual(self.found('switch_page'), [])

    def test_none_and_empty_inputs_are_safe(self):
        self.ui.render_page_header(None)
        self.ui.render_overview(None)
        self.ui.render_details({})
        self.ui.render_progress(None)
        self.ui.render_status(None)
        self.ui.render_empty_state(None)
        self.ui.render_app_nav('home', keys=())
        self.assert_safe_native_calls()
        self.assertEqual(self.found('success'), [])
        self.assertEqual(self.found('error'), [])


if __name__ == '__main__':
    unittest.main()
