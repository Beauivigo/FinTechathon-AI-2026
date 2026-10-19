"""Codex固定验收：输入矩阵、纯函数边界、澄清和草稿隔离。"""
import ast
from copy import deepcopy
import json
from pathlib import Path
import unittest

from agent.routing import route

ROOT = Path(__file__).resolve().parents[1]


class RoutingAcceptanceTests(unittest.TestCase):
    def test_fixed_matrix(self):
        cases = json.loads((ROOT/'docs/evidence/2026-10-19-routing-cases.json').read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(text=case['text']):
                result = route(case['text'])
                self.assertEqual(result['status'], case['status'])
                self.assertEqual(result['intent'], case['intent'])
                self.assertEqual(set(result), {'status','intent','fields','missing','message'})
                self.assertIsInstance(result['fields'], dict)
                self.assertIsInstance(result['missing'], list)
                self.assertTrue(result['message'])

    def test_explicit_fields_and_no_default_account(self):
        r = route('从账户 demo_account_a 给李明转200元')
        self.assertEqual(r['fields']['account_id'], 'demo_account_a')
        self.assertEqual(r['fields']['payee_query'], '李明')
        self.assertEqual(r['fields']['amount_text'], '200元')
        r = route('给李明转200元')
        self.assertNotIn('account_id', r['fields'])
        self.assertIn('account_id', r['missing'])

    def test_followup_and_input_immutability(self):
        draft = route('帮我准备生日'); original = deepcopy(draft)
        result = route('2026-11-20', draft)
        self.assertEqual((result['intent'], result['status']), ('birthday','ready'))
        self.assertEqual(result['fields']['birthday'], '2026-11-20')
        self.assertEqual(draft, original)
        draft = route('给李明转钱')
        draft = route('账户 demo_account_a', draft)
        result = route('200元', draft)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['fields']['payee_query'], '李明')

    def test_clear_and_new_intent_do_not_keep_fields(self):
        draft = route('给李明转钱')
        for text in ('取消草稿','不要转账','算了'):
            r = route(text, draft)
            self.assertEqual(r['status'], 'cancelled')
            self.assertEqual(r['fields'], {})
        r = route('帮我准备生日', draft)
        self.assertEqual(r['intent'], 'birthday')
        self.assertNotIn('payee_query', r['fields'])
        self.assertEqual(route('取消订阅 SUB-E001')['intent'], 'subscription')

    def test_invalid_or_ambiguous_values_never_ready(self):
        for text in ('从账户 demo_account_a 给李明转-200元',
                     '从账户 demo_account_a 给李明转0元',
                     '从账户 demo_account_a 给李明转2 00元',
                     '从账户 demo_account_a 给李明转2\t10元',
                     '从账户 demo_account_a 给李明转-' + ' '*20 + '200元',
                     '从账户 demo_account_a 给李明转200.123元',
                     '生日2026-11-20或2026-12-20',
                     '冻结卡片 CARD-E001 或 CARD-E002',
                     '查账单然后给李明转200元',
                     '批准卡片申请并发卡'):
            with self.subTest(text=text):
                self.assertNotEqual(route(text)['status'], 'ready')
        for text in (None, 12, [], ' ', '字'*201):
            self.assertEqual(route(text)['status'], 'unsupported')
        self.assertNotEqual(route('确认',route('给李明转钱'))['status'], 'ready')

    def test_module_has_no_business_or_io_dependencies(self):
        import agent.routing as module
        tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split('.')[0], {'re','datetime','copy','typing'})
            elif isinstance(node, ast.ImportFrom):
                self.assertIn((node.module or '').split('.')[0], {'re','datetime','copy','typing','__future__'})
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {'open','exec','eval','compile','__import__'})

    def test_conflicting_values_cannot_reappear_after_followup(self):
        draft = route('给李明转200或300元')
        self.assertEqual(draft['status'], 'clarify')
        self.assertNotIn('amount_text', draft['fields'])
        self.assertNotEqual(route('账户 demo_account_a', draft)['status'], 'ready')
        draft = route('给李明转200元')
        draft = route('0元', draft)
        self.assertNotEqual(route('账户 demo_account_a', draft)['status'], 'ready')
        self.assertEqual(route('批准发卡 账户 demo_account_a')['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
