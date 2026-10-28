'''Contract tests for the display-only bank home view using a standard-library fake streamlit.'''

from __future__ import annotations

import builtins
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
ROOT = _HERE if (_HERE / 'app').is_dir() else _HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Block:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeStreamlit(types.ModuleType):
    '''Record display calls and simulate one button click without session state.'''

    _DISPLAY_METHODS = {
        'title', 'header', 'subheader', 'caption', 'write', 'markdown',
        'info', 'warning', 'divider', 'table', 'metric',
    }

    def __init__(self):
        super().__init__('streamlit')
        self.calls = []
        self.clicked_label = None
        self.session_state_accesses = 0

    @property
    def session_state(self):
        self.session_state_accesses += 1
        raise AssertionError('home_view must not use st.session_state')

    def __getattr__(self, name):
        if name in self._DISPLAY_METHODS:
            def record(*args, **kwargs):
                self.calls.append((name, args, kwargs))
            return record
        raise AttributeError(name)

    def _record(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))

    def button(self, label, *args, **kwargs):
        self._record('button', (label,) + args, kwargs)
        return self.clicked_label == label

    def columns(self, spec, *args, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        if not 1 <= count <= 4:
            raise AssertionError(f'home_view requested {count} columns; maximum is 4')
        self._record('columns', (spec,) + args, kwargs)
        return tuple(_Block() for _ in range(count))

    def container(self, *args, **kwargs):
        self._record('container', args, kwargs)
        return _Block()

    def expander(self, *args, **kwargs):
        self._record('expander', args, kwargs)
        return _Block()


def _flatten(value):
    if isinstance(value, dict):
        return ' '.join(f'{key} {_flatten(item)}' for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return ' '.join(_flatten(item) for item in value)
    return str(value)


def _text(fake):
    return ' | '.join(_flatten(args) for _name, args, _kwargs in fake.calls)


def _buttons(fake):
    return [call for call in fake.calls if call[0] == 'button'
            and call[2].get('key', '').startswith('home_service_')]


def _button_labels(fake):
    return [call[1][0] for call in _buttons(fake)]


def _column_counts(fake):
    counts = []
    for name, args, _kwargs in fake.calls:
        if name == 'columns':
            spec = args[0]
            counts.append(spec if isinstance(spec, int) else len(spec))
    return counts


class HomeViewContractTests(unittest.TestCase):
    def setUp(self):
        self._had_streamlit = 'streamlit' in sys.modules
        self._previous_streamlit = sys.modules.get('streamlit')
        self.fake = FakeStreamlit()
        sys.modules['streamlit'] = self.fake
        self._drop_home_module()
        self.assertTrue((ROOT / 'app' / 'home_view.py').is_file())
        self.hv = importlib.import_module('app.home_view')

    def tearDown(self):
        self._drop_home_module()
        if self._had_streamlit:
            sys.modules['streamlit'] = self._previous_streamlit
        else:
            sys.modules.pop('streamlit', None)

    def _drop_home_module(self):
        sys.modules.pop('app.home_view', None)
        app_module = sys.modules.get('app')
        if app_module is not None:
            try:
                delattr(app_module, 'home_view')
            except AttributeError:
                pass

    def _base_data(self):
        hv = self.hv
        return hv.HomeData(
            account=hv.AccountSummary(
                name='日常账户',
                balance_text='¥1,234.56',
                reserved_text='¥200.00',
            ),
            pending_count=1,
            todo_items=('核对待确认转账',),
            transactions=(
                hv.TransactionSummary(
                    title='社区超市',
                    time_text='10月17日 09:20',
                    amount_text='-¥38.50',
                    status_text='已完成',
                ),
            ),
            notice=None,
        )

    def test_renders_supplied_records_and_service_entries(self):
        hv = self.hv
        data = hv.HomeData(
            account=hv.AccountSummary(
                name='日常储蓄账户',
                balance_text='¥1,234.56',
                reserved_text='¥200.00',
            ),
            pending_count=2,
            todo_items=('确认一笔待处理转账',),
            transactions=(
                hv.TransactionSummary(
                    title='社区超市',
                    time_text='10月17日 09:20',
                    amount_text='-¥38.50',
                    status_text='已完成',
                ),
            ),
            notice='数据来自本机模拟账户。',
        )
        self.assertIsNone(hv.render_home(data))
        text = _text(self.fake)
        expected_fragments = (
            '首页',
            '本地模拟',
            '账户概览',
            '日常储蓄账户',
            '余额',
            '¥1,234.56',
            '预留金额',
            '¥200.00',
            '常用业务',
            '待处理事项',
            '有 2 项业务待处理。',
            '确认一笔待处理转账',
            '近期交易',
            '社区超市',
            '10月17日 09:20',
            '-¥38.50',
            '已完成',
            '数据来自本机模拟账户。',
            '使用说明',
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)
        expected_labels = [action.label for action in hv.SERVICE_ACTIONS]
        self.assertEqual(_button_labels(self.fake), expected_labels)
        for _name, _args, kwargs in _buttons(self.fake):
            self.assertEqual(kwargs.get('width'), 'stretch')
            self.assertNotIn('use_container_width', kwargs)
        self.assertLessEqual(max(_column_counts(self.fake), default=0), 4)
        self.assertEqual(self.fake.session_state_accesses, 0)
        self.assertNotIn('资产总额', text)

    def test_unknown_values_are_reported_without_fake_numbers_or_success(self):
        hv = self.hv
        data = hv.HomeData(
            account=hv.AccountSummary(),
            pending_count=None,
            todo_items=None,
            transactions=None,
        )
        self.assertIsNone(hv.render_home(data))
        text = _text(self.fake)
        expected_fragments = (
            '账户名称暂不可用',
            '余额',
            '预留金额',
            '待处理数量暂不可用。',
            '待办内容暂不可用。',
            '近期交易暂不可用。',
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)
        for forbidden in (
            '¥0.00',
            '资产总额',
            '总资产',
            '暂无近期交易。',
            '当前没有待处理事项。',
            '成功',
        ):
            self.assertNotIn(forbidden, text)
        self.assertEqual(self.fake.session_state_accesses, 0)

        self.fake.calls.clear()
        self.assertIsNone(hv.render_home(None))
        missing_text = _text(self.fake)
        for fragment in (
            '账户信息暂不可用。',
            '待处理数量暂不可用。',
            '待办内容暂不可用。',
            '近期交易暂不可用。',
        ):
            self.assertIn(fragment, missing_text)
        self.assertNotIn('¥0.00', missing_text)
        self.assertNotIn('成功', missing_text)

    def test_empty_collections_are_distinct_from_unavailable_ones(self):
        hv = self.hv
        data = hv.HomeData(
            account=hv.AccountSummary(
                name='日常账户',
                balance_text='¥10.00',
                reserved_text='¥0.00',
            ),
            pending_count=0,
            todo_items=(),
            transactions=(),
        )
        self.assertIsNone(hv.render_home(data))
        text = _text(self.fake)
        for fragment in ('当前没有待处理事项。', '暂无近期交易。'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)
        for unavailable in (
            '待处理数量暂不可用。',
            '待办内容暂不可用。',
            '近期交易暂不可用。',
        ):
            self.assertNotIn(unavailable, text)
        self.assertIn('¥0.00', text)

    def test_transaction_unknown_fields_are_explicit(self):
        hv = self.hv
        data = hv.HomeData(
            account=hv.AccountSummary(
                name='日常账户',
                balance_text='¥10.00',
                reserved_text='¥0.00',
            ),
            pending_count=0,
            todo_items=(),
            transactions=(
                hv.TransactionSummary(
                    title='待核对交易',
                    time_text=None,
                    amount_text=None,
                    status_text=None,
                ),
            ),
        )
        self.assertIsNone(hv.render_home(data))
        text = _text(self.fake)
        for fragment in ('待核对交易', '时间暂不可用', '金额暂不可用', '状态未知'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)
        self.assertNotIn('成功', text)
        self.assertEqual(self.fake.session_state_accesses, 0)

    def test_clicked_service_button_returns_only_its_action_id(self):
        hv = self.hv
        expected = [
            ('transfer', '转账'),
            ('bills', '收支明细'),
            ('cards', '卡片管理'),
            ('aa', 'AA收款'),
            ('scheduled', '预约转账'),
            ('budget', '预算管理'),
            ('wealth', '理财'),
            ('products', '产品对比'),
            ('subscriptions','订阅管理'), ('merchant','生日计划'), ('mine','我的'),
        ]
        self.assertEqual(
            [(item.action_id, item.label) for item in hv.SERVICE_ACTIONS],
            expected,
        )
        expected_labels = [label for _action_id, label in expected]
        expected_ids = [action_id for action_id, _label in expected]
        data = self._base_data()
        for action_id, label in expected:
            with self.subTest(action_id=action_id):
                self.fake.clicked_label = label
                start = len(self.fake.calls)
                self.assertEqual(hv.render_home(data), action_id)
                calls = self.fake.calls[start:]
                buttons = [call for call in calls if call[0] == 'button'
                           and call[2].get('key', '').startswith('home_service_')]
                self.assertEqual([call[1][0] for call in buttons], expected_labels)
                keys = [call[2].get('key') for call in buttons]
                self.assertTrue(all(isinstance(key, str) and key for key in keys))
                self.assertEqual(len(set(keys)), len(expected_labels))
                for index, (_name, _args, kwargs) in enumerate(buttons):
                    self.assertIn(expected_ids[index], kwargs['key'])
                    self.assertEqual(kwargs.get('width'), 'stretch')
                self.assertFalse(
                    {call[0] for call in calls} & {'switch_page', 'rerun', 'toast'}
                )
                self.assertEqual(self.fake.session_state_accesses, 0)

    def test_repeated_render_has_no_hidden_selection_or_data_mutation(self):
        hv = self.hv
        data = self._base_data()
        self.assertIsNone(hv.render_home(data))
        self.assertIsNone(hv.render_home(data))
        self.fake.clicked_label = '转账'
        self.assertEqual(hv.render_home(data), 'transfer')
        self.fake.clicked_label = None
        self.assertIsNone(hv.render_home(data))
        self.assertEqual(data, self._base_data())
        self.assertEqual(len(_buttons(self.fake)), 4 * len(hv.SERVICE_ACTIONS))
        self.assertEqual(self.fake.session_state_accesses, 0)

    def test_assistant_entry_returns_navigation_without_business_or_state(self):
        self.fake.clicked_label = '打开 AI 银行助手'
        self.assertEqual(self.hv.render_home(self._base_data()), 'assistant')
        assistant = [call for call in self.fake.calls if call[0] == 'button'
                     and call[2].get('key') == 'home_assistant']
        self.assertEqual(len(assistant), 1)
        self.assertEqual(self.fake.session_state_accesses, 0)
        self.assertFalse({name for name, _, _ in self.fake.calls} & {'switch_page', 'rerun'})

    def test_import_and_render_surface_are_display_only(self):
        original_import = builtins.__import__
        forbidden_hits = []
        forbidden_roots = {
            'bank', 'agent', 'shared', 'storage', 'database', 'db', 'pages',
        }

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if any(part in forbidden_roots for part in name.split('.')):
                forbidden_hits.append(name)
                raise AssertionError(f'forbidden import: {name}')
            return original_import(name, globals, locals, fromlist, level)

        self._drop_home_module()
        with mock.patch('builtins.__import__', new=guarded_import):
            module = importlib.import_module('app.home_view')
        self.assertEqual(forbidden_hits, [])
        data = module.HomeData(
            account=module.AccountSummary(
                name='模拟账户',
                balance_text='¥1.00',
                reserved_text='¥0.00',
            ),
            pending_count=0,
            todo_items=(),
            transactions=(),
        )
        self.fake.clicked_label = '转账'
        self.assertEqual(module.render_home(data), 'transfer')
        method_names = {name for name, _args, _kwargs in self.fake.calls}
        forbidden_methods = {
            'switch_page', 'rerun', 'toast', 'form', 'form_submit_button',
            'download_button', 'text_input', 'selectbox', 'radio', 'checkbox',
            'toggle', 'file_uploader', 'chat_input', 'chat_message', 'html',
            'components',
        }
        self.assertFalse(method_names & forbidden_methods)
        self.assertLessEqual(max(_column_counts(self.fake), default=0), 4)
        self.assertEqual(self.fake.session_state_accesses, 0)

    def test_docs_wire_every_shortcut_to_a_concrete_destination(self):
        doc_path = ROOT / 'docs' / 'UI首页改版.md'
        self.assertTrue(doc_path.is_file(), f'missing design/integration doc: {doc_path}')
        doc = doc_path.read_text(encoding='utf-8')
        self.assertIn('render_home', doc)
        targets = {
            'transfer': '主脚本',
            'bills': '收支明细',
            'cards': 'pages/3_卡片管理.py',
            'aa': 'pages/4_AA收款.py',
            'scheduled': 'pages/6_定时转账.py',
            'budget': 'pages/7_预算管理.py',
            'wealth': 'pages/9_理财.py',
            'products': 'pages/2_产品对比.py',
            'subscriptions':'pages/5_订阅管理.py', 'merchant':'pages/8_生日与商户.py','mine':'pages/10_我的.py',
        }
        for action in self.hv.SERVICE_ACTIONS:
            with self.subTest(action_id=action.action_id):
                self.assertIn(action.action_id, doc)
                self.assertIn(action.label, doc)
                self.assertIn(targets[action.action_id], doc)


if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
