'''视觉系统冻结测试：设计 token、静态样式与模块表面接线。

只使用标准库与最小 fake streamlit，不启动真实页面，也不冒充浏览器验收。
覆盖：三种模块原色、共用语义对比度、静态样式可信性（无外链/脚本/隐藏控件）、
减少动态效果与无模糊回退、固定容器 key 的唯一性、纯展示边界。
'''
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VISUAL_PATH = ROOT / 'app' / 'visual.py'
UI_PATH = ROOT / 'app' / 'ui.py'
HOME_VIEW_PATH = ROOT / 'app' / 'home_view.py'


class _Block:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeStreamlit(types.ModuleType):
    '''Record calls made by the display-only modules.'''

    def __init__(self):
        super().__init__('streamlit')
        self.calls = []

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name in {'container', 'columns', 'expander', 'sidebar', 'form'}:
                return _Block() if name != 'columns' else [_Block() for _ in range(3)]
            return None

        return call


def _load(path, module_name, fake):
    previous = sys.modules.get('streamlit')
    sys.modules['streamlit'] = fake
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError('cannot load ' + str(path))
        module = importlib.util.module_from_spec(spec)
        # dataclasses 在装饰期间会按 __module__ 回查 sys.modules，必须先行注册。
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop('streamlit', None)
        else:
            sys.modules['streamlit'] = previous
    return module


def _luminance(color):
    values = []
    for index in (1, 3, 5):
        channel = int(color[index:index + 2], 16) / 255.0
        values.append(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]


def _contrast(first, second):
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _string_arg(node, default=None):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return default


def _surface_calls():
    '''Return (path, system, kind, name) for every literal visual surface call.'''
    records = []
    files = sorted((ROOT / 'app').rglob('*.py')) + [ROOT / 'app' / 'main.py']
    seen_paths = set()
    for path in files:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == 'visual'):
                continue
            kind = {'card': 'card', 'band': 'band', 'card_key': 'card-key'}.get(func.attr)
            if kind is None:
                continue
            system = _string_arg(node.args[0]) if node.args else None
            name = _string_arg(node.args[1]) if len(node.args) > 1 else ''
            if func.attr == 'band' and not name:
                name = 'header'
            records.append((path.relative_to(ROOT).as_posix(), system, kind, name))
    return records


class VisualTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.st = FakeStreamlit()
        cls.visual = _load(VISUAL_PATH, 'visual_under_test', cls.st)
        cls.ui = _load(UI_PATH, 'ui_under_test_visual', FakeStreamlit())
        cls.home = _load(HOME_VIEW_PATH, 'home_view_under_test_visual', FakeStreamlit())

    def setUp(self):
        self.st.calls.clear()

    def test_three_module_palettes_are_the_requested_pairs(self):
        expected = {
            'core': ('#FFFFD0', '#D8EFFF'),
            'ai': ('#DDFFC2', '#EEE1FF'),
            'bill': ('#FFEAE1', '#B6FAFF'),
        }
        self.assertEqual(set(self.visual.MODULES), set(expected))
        for system, (base, accent) in expected.items():
            module = self.visual.MODULES[system]
            self.assertEqual(module['base'], base)
            self.assertEqual(module['accent'], accent)
            self.assertIn(base, self.visual.STYLE)
            self.assertIn(accent, self.visual.STYLE)
            self.assertTrue(module['label'])
            for kind in ('card', 'band'):
                self.assertIn(f'st-key-{self.visual.KEY_PREFIX}-{system}-{kind}-', self.visual.STYLE)

    def test_shared_semantics_and_contrast(self):
        colors = self.visual.COLORS
        self.assertEqual(colors['primary'], self.ui.PALETTE['primary'])
        self.assertEqual(colors['ink'], self.ui.PALETTE['ink'])
        self.assertEqual(colors['danger'], self.ui.PALETTE['danger'])
        self.assertEqual(self.ui.THEME_TOKENS['primaryColor'], colors['primary'])
        self.assertEqual(self.visual.FONT_STACK, self.ui.FONT_STACK)
        self.assertGreaterEqual(_contrast(colors['ink'], colors['paper']), 7.0)
        self.assertGreaterEqual(_contrast('#FFFFFF', colors['primary']), 4.5)
        self.assertGreaterEqual(_contrast(colors['muted'], colors['surface']), 4.5)
        self.assertGreaterEqual(_contrast(colors['danger'], colors['surface']), 4.5)
        self.assertGreaterEqual(_contrast(colors['success'], colors['surface']), 4.5)
        self.assertGreaterEqual(_contrast(colors['warning'], colors['surface']), 4.5)
        # 三种模块原色上的深色文字都必须可读
        for system, module in self.visual.MODULES.items():
            self.assertGreaterEqual(_contrast(colors['ink'], module['base']), 7.0, system)
            self.assertGreaterEqual(_contrast(colors['ink'], module['accent']), 7.0, system)

    def test_style_is_local_static_and_never_hides_controls(self):
        style = self.visual.STYLE
        self.assertEqual(style.count('{'), style.count('}'))
        self.assertEqual(style.count('('), style.count(')'))
        for banned in ('<script', 'javascript:', 'url(', '@import', 'http://', 'https://',
                       'fonts.googleapis', 'unsafe_allow_html', 'expression(', 'data-baseweb'):
            # data-baseweb：Streamlit 1.65 的输入控件改用自有 testid 组件，
            # BaseWeb 选择器不会命中，因此样式里禁止出现这种失效写法。
            self.assertNotIn(banned, style)
        # Only the two empty navigation surfaces on authentication entry pages
        # may be hidden. Forms, buttons and all business controls stay visible.
        entry_only_rule = re.compile(
            r'body:has\(\.st-key-vx-auth-entry\)\s+\[data-testid="stSidebar"\],\s*'
            r'body:has\(\.st-key-vx-auth-entry\)\s+\[data-testid="stSidebarCollapsedControl"\]\s*'
            r'\{\s*display:\s*none\s*!important;\s*\}'
        )
        self.assertEqual(len(entry_only_rule.findall(style)), 1)
        default_style = entry_only_rule.sub('', style)
        self.assertIsNone(re.search(
            r'display\s*:\s*none|visibility\s*:\s*hidden|pointer-events\s*:\s*none|clip-path',
            default_style, re.IGNORECASE), 'a rule hides content outside the scoped empty auth navigation')
        self.assertIsNone(re.search(r'\{[a-z_]+\}', style), 'style keeps an unformatted placeholder')

    def test_style_covers_core_surfaces_and_accessibility(self):
        style = self.visual.STYLE
        for selector in ('[data-testid="stBaseButton-primary"]', '[data-testid="stMetric"]',
                         '[data-testid="stTabs"]', '[data-testid="stExpander"]',
                         '[data-testid="stForm"]', '[data-testid="stDataFrame"]',
                         '[data-testid="stSidebar"]', '[data-testid="stAlertContainer"]',
                         '[data-testid="stChatMessage"]', '[data-testid="stMainBlockContainer"]',
                         '[data-testid="stCaptionContainer"]', '[data-testid="stMarkdownContainer"]'):
            self.assertIn(selector, style)
        self.assertIn('@media (prefers-reduced-motion: reduce)', style)
        self.assertIn('@supports (backdrop-filter', style)
        self.assertIn('min-height: 44px', style)
        self.assertIn('focus-visible', style)
        self.assertIn('outline: 2px solid', style)
        self.assertIn('font-family: IBM Plex Sans', style)
        for width in ('@media (max-width: 1024px)', '@media (max-width: 768px)', '@media (max-width: 420px)'):
            self.assertIn(width, style)
        self.assertIn(self.visual.CONTENT_WIDTH, style)
        self.assertIn('tabular-nums', style)
        self.assertIn('white-space: normal', style)

    def test_surface_helpers_use_stable_prefixed_keys(self):
        visual = self.visual
        self.assertEqual(visual.card_key('ai', 'Transfer Chat'), 'vx-ai-card-transfer-chat')
        self.assertEqual(visual.card_key('core', 'home-account', kind='band'), 'vx-core-band-home-account')
        self.assertEqual(visual.card_key('unknown', 'x'), 'vx-core-card-x')
        self.assertEqual(visual.card_key('bill', '  '), 'vx-bill-card-surface')
        self.assertEqual(visual.card_key('bill', 'x', kind='nope'), 'vx-bill-card-x')
        container = visual.card('bill', 'ledger')
        self.assertEqual(container.__class__.__name__, '_Block')
        kwargs = self.st.calls[-1][2]
        self.assertTrue(kwargs['border'])
        self.assertEqual(kwargs['key'], 'vx-bill-card-ledger')
        visual.band('ai', 'header')
        self.assertEqual(self.st.calls[-1][2]['key'], 'vx-ai-band-header')

    def test_apply_inserts_only_fixed_style_text(self):
        self.visual.apply('ai')
        calls = [call for call in self.st.calls if call[0] == 'html']
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0][1], calls[0][2]
        body = args[0]
        self.assertEqual(kwargs, {})
        self.assertTrue(body.startswith('<style>'))
        self.assertTrue(body.endswith('</style>'))
        self.assertIn(self.visual.STYLE, body)
        self.assertIn(self.visual.MODULES['ai']['wash_base'], body)
        self.assertNotIn('<script', body)
        self.st.calls.clear()
        self.visual.apply('not-a-module')
        body = [call for call in self.st.calls if call[0] == 'html'][0][1][0]
        self.assertIn(self.visual.MODULES['core']['wash_base'], body)

    def test_visual_module_imports_and_helpers_stay_display_only(self):
        tree = ast.parse(VISUAL_PATH.read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        self.assertLessEqual(imported, {'__future__', 're', 'streamlit'})
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'st':
                self.assertIn(node.attr, {'container', 'html'})

    def test_every_page_declares_one_module_surface_and_header_band(self):
        calls = _surface_calls()
        self.assertTrue(calls)
        for path, system, kind, name in calls:
            self.assertIn(system, self.visual.MODULES, f'{path}: unknown module {system!r}')
            self.assertTrue(name, f'{path}: empty surface name for {kind}')
            self.assertRegex(name, r'^[a-z0-9-]+$', f'{path}: unstable surface name {name!r}')
        pages = sorted((ROOT / 'app' / 'pages').glob('*.py')) + [ROOT / 'app' / 'main.py']
        for path in pages:
            text = path.read_text(encoding='utf-8')
            self.assertIn('visual.apply(', text, path.name)
            self.assertIn('visual.band(', text, path.name)
            self.assertNotIn('st.html(', text, path.name)

    def test_surface_keys_are_unique_per_script(self):
        seen = {}
        for path, system, kind, name in _surface_calls():
            family = 'band' if kind == 'band' else 'card'
            key = self.visual.card_key(system, name, kind=family)
            bucket = seen.setdefault(path, set())
            self.assertNotIn(key, bucket, f'duplicate visual surface {key!r} in {path}')
            bucket.add(key)

    def test_each_script_configures_page_before_theming(self):
        files = sorted((ROOT / 'app' / 'pages').glob('*.py')) + [ROOT / 'app' / 'main.py']
        for path in files:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            calls = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and isinstance(node.func.value, ast.Name) and node.func.value.id in {'st', 'visual'}:
                    calls.append((node.lineno, node.func.value.id + '.' + node.func.attr))
            calls.sort()
            self.assertTrue(calls, path.name)
            self.assertEqual(calls[0][1], 'st.set_page_config', path.name)
            self.assertEqual(sum(1 for _line, name in calls if name == 'st.set_page_config'), 1, path.name)
            applies = [line for line, name in calls if name == 'visual.apply']
            self.assertEqual(len(applies), 1, path.name)
            self.assertGreater(applies[0], calls[0][0], path.name)
            text = path.read_text(encoding='utf-8')
            self.assertTrue('render_page_header' in text or 'st.title(' in text, path.name)

    def test_home_view_uses_fixed_surface_keys_without_style_injection(self):
        text = HOME_VIEW_PATH.read_text(encoding='utf-8')
        keys = re.findall(r"'(vx-[a-z0-9-]+)'", text)
        self.assertTrue(keys)
        self.assertEqual(len(keys), len(set(keys)))
        for key in keys:
            self.assertRegex(key, r'^vx-(core|ai|bill)-(card|band)-[a-z0-9-]+$')
        self.assertNotIn('visual.apply', text)
        self.assertNotIn('st.html', text)
        self.assertNotIn('session_state', text)
        self.assertNotIn('switch_page', text)


if __name__ == '__main__':
    unittest.main()
