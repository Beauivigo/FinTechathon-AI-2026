"""Review tests for routing boundaries, wealth labels, and wealth-page input safety."""
import ast
from decimal import Decimal, InvalidOperation
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.routing import route

PANEL_PATH = ROOT / 'app' / 'routing_panel.py'
PAGE_PATH = ROOT / 'app' / 'pages' / '9_理财.py'


def _load_panel():
    previous = sys.modules.get('streamlit')
    if previous is None:
        sys.modules['streamlit'] = types.ModuleType('streamlit')
    try:
        spec = importlib.util.spec_from_file_location('review_routing_panel', PANEL_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop('streamlit', None)


PANEL = _load_panel()


def _page_namespace(names):
    tree = ast.parse(PAGE_PATH.read_text(encoding='utf-8'))
    definitions = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            definitions[node.name] = node
    missing = [name for name in names if name not in definitions]
    if missing:
        raise AssertionError('missing page helpers: ' + ', '.join(missing))
    module = ast.Module(body=[definitions[name] for name in names], type_ignores=[])
    namespace = {'Decimal': Decimal, 'InvalidOperation': InvalidOperation}
    exec(compile(module, str(PAGE_PATH), 'exec'), namespace)
    return namespace, tree


class RoutingReviewTests(unittest.TestCase):
    def test_bare_negations_cancel_and_multi_intent_clarifies(self):
        for text in ('不查账单', '不冻结卡片 CARD-E001', '不取消订阅 SUB-E001',
                     '不转账给李明', '不购买理财产品'):
            with self.subTest(text=text):
                result = route(text)
                self.assertEqual(result['status'], 'cancelled')
                self.assertIsNone(result['intent'])
                self.assertEqual(result['fields'], {})
        for text in ('查账单然后冻结卡片', '查账单并查余额'):
            with self.subTest(text=text):
                result = route(text)
                self.assertEqual(result['status'], 'clarify')
                self.assertIsNone(result['intent'])
                self.assertEqual(result['fields'], {})
                self.assertTrue(result['message'])

    def test_conjoined_or_ranged_amounts_never_ready(self):
        for amount in ('200和300元', '200 与 300 元', '200—300元', '200至300元'):
            with self.subTest(amount=amount):
                result = route('从账户 demo_account_a 给李明转' + amount)
                self.assertEqual(result['status'], 'clarify')
                self.assertEqual(result['intent'], 'transfer')
                self.assertNotIn('amount_text', result['fields'])

    def test_followup_conflicting_amount_does_not_leak_or_mutate(self):
        draft = route('账户 demo_account_a 给李明转钱')
        self.assertEqual(draft['status'], 'clarify')
        self.assertEqual(draft['fields']['account_id'], 'demo_account_a')
        self.assertEqual(draft['fields']['payee_query'], '李明')
        result = route('200和300元', draft)
        self.assertEqual(result['status'], 'clarify')
        self.assertNotIn('amount_text', result['fields'])
        self.assertEqual(draft['fields']['account_id'], 'demo_account_a')
        valid = route('200元', draft)
        self.assertEqual(valid['status'], 'ready')
        self.assertEqual(valid['fields']['amount_text'], '200元')

    def test_valid_decimal_amount_remains_supported(self):
        result = route('从账户 demo_account_a 给李明转200.5元')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['fields']['amount_text'], '200.5元')


class WealthRoutingDisplayTests(unittest.TestCase):
    def test_all_wealth_intents_are_labeled_and_handoff_enabled(self):
        expected = {'wealth_assess': '风险评估', 'wealth_purchase': '购买理财产品',
                    'wealth_redeem': '赎回理财', 'wealth_holdings': '查理财持仓'}
        labels = []
        for intent, text in expected.items():
            with self.subTest(intent=intent):
                self.assertIn(intent, PANEL._INTENTS)
                label = PANEL._intent_label(intent)
                self.assertNotEqual(label, '未识别')
                self.assertIn('理财', label)
                labels.append(label)
                result = route(text)
                self.assertEqual(result['intent'], intent)
                self.assertIn(result['status'], ('ready', 'clarify'))
        self.assertEqual(len(set(labels)), 4)

    def test_wealth_fields_have_readable_labels_and_no_invented_values(self):
        for key in ('product_id', 'units_text', 'questionnaire_version'):
            with self.subTest(key=key):
                label = PANEL._label(key)
                self.assertTrue(label)
                self.assertNotEqual(label, '补充信息')
        purchase = route('购买理财产品')
        self.assertEqual(purchase['status'], 'clarify')
        self.assertNotIn('product_id', purchase['fields'])
        self.assertIn('product_id', purchase['missing'])
        for key in purchase['missing']:
            self.assertNotEqual(PANEL._label(key), '补充信息')
        redeem = route('赎回理财')
        self.assertEqual(redeem['status'], 'clarify')
        self.assertNotIn('units_text', redeem['fields'])
        self.assertIn('units_text', redeem['missing'])
        for key in redeem['missing']:
            self.assertNotEqual(PANEL._label(key), '补充信息')
        assess = route('风险评估')
        self.assertEqual(assess['fields'], {'questionnaire_version': 'wpq_v1'})


class WealthPageSafetyTests(unittest.TestCase):
    def test_exact_conversion_rejects_extra_precision(self):
        namespace, _ = _page_namespace(('_minor_from_yuan', '_units_from_input', '_units_widget_key'))
        minor = namespace['_minor_from_yuan']
        units = namespace['_units_from_input']
        key = namespace['_units_widget_key']
        self.assertEqual(minor(100.0), 10000)
        self.assertEqual(minor(1.23), 123)
        self.assertIsNone(minor(1.005))
        self.assertIsNone(minor(-1))
        self.assertIsNone(minor(0))
        self.assertEqual(units(0.0001), '0.0001')
        self.assertEqual(units(1.2), '1.2000')
        self.assertEqual(units(1.2345), '1.2345')
        self.assertIsNone(units(1.23456))
        self.assertIsNone(units(0))
        first = key('HOLD-1', '100.0000')
        second = key('HOLD-2', '100.0000')
        third = key('HOLD-1', '50.0000')
        self.assertIn('HOLD-1', first)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)

    def test_page_keeps_blocked_gateway_buttons_and_safe_input_wiring(self):
        _, tree = _page_namespace(('_minor_from_yuan', '_units_from_input', '_units_widget_key'))
        called = set()
        buttons = {}
        number_input_key_calls = set()
        actions = set()
        quantize_calls = 0
        workflow_imports = set()
        assignments = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == 'app.workflow':
                workflow_imports.update(alias.name for alias in node.names)
            if (isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments[target.id] = node.value.func.id
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Name):
                called.add(function.id)
            if isinstance(function, ast.Attribute):
                if function.attr == 'quantize':
                    quantize_calls += 1
                if function.attr == 'button':
                    key_value = None
                    blocked = False
                    for keyword in node.keywords:
                        if keyword.arg == 'key' and isinstance(keyword.value, ast.Constant):
                            key_value = keyword.value.value
                        if (keyword.arg == 'disabled'
                                and any(isinstance(part, ast.Attribute) and part.attr=='blocked'
                                        and isinstance(part.value, ast.Name) and part.value.id=='flow'
                                        for part in ast.walk(keyword.value))):
                            blocked = True
                    if key_value is not None:
                        buttons[key_value] = buttons.get(key_value, False) or blocked
                if function.attr in ('number_input','text_input'):
                    for keyword in node.keywords:
                        if (keyword.arg == 'key'
                                and isinstance(keyword.value, ast.Call)
                                and isinstance(keyword.value.func, ast.Name)):
                            number_input_key_calls.add(keyword.value.func.id)
            if isinstance(function, ast.Name) and function.id == 'perform' and len(node.args) >= 2:
                first, second = node.args[0], node.args[1]
                if (isinstance(first, ast.Attribute) and first.attr == 'start_action'
                        and isinstance(first.value, ast.Name) and first.value.id == 'flow'
                        and isinstance(second, ast.Constant)):
                    actions.add(second.value)
        for key in ('wealth_assess', 'wealth_purchase', 'wealth_redeem'):
            self.assertTrue(buttons.get(key), key)
        self.assertIn('_units_widget_key', number_input_key_calls)
        self.assertEqual(quantize_calls, 0)
        self.assertIn('_minor_from_yuan', called)
        self.assertIn('_units_from_input', called)
        self.assertIn('_units_widget_key', called)
        self.assertEqual(assignments.get('amount_minor'), '_minor_from_yuan')
        self.assertEqual(assignments.get('units_text'), '_units_from_input')
        self.assertTrue({'wealth.assess', 'wealth.purchase', 'wealth.redeem'} <= actions)
        self.assertTrue({'current', 'render', 'perform'} <= workflow_imports)


if __name__ == '__main__':
    unittest.main()
