"""随仓库分发的已核验模拟资料；不依赖成员电脑目录。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from bank.seed import initialize
from bank.store import Store
from bank.gateway import BankGateway
from material_check import approved
from material_check.reader import read_workbook
from material_check.reconcile import reconcile_material
from shared import adapt_request
from streamlit.testing.v1 import AppTest


class ReleaseMaterialsTests(unittest.TestCase):
    def test_six_account_months_and_hand_calculations(self):
        self.assertTrue(approved.SOURCE.is_relative_to(approved.ROOT))
        self.assertTrue(approved.DECISION.is_relative_to(approved.ROOT))
        expected = {
            ('ACCT-C001','08'): (46490,0,{'交通':5000,'日用百货':32850,'餐饮':8640}),
            ('ACCT-C001','09'): (0,4500,{}),
            ('ACCT-C002','08'): (2500,29900,{'影音会员':2500}),
            ('ACCT-C002','09'): (256800,0,{'住房':250000,'云服务':6800}),
            ('ACCT-C003','08'): (0,0,{}),
            ('ACCT-C003','09'): (41260,0,{'日用百货':41260})}
        ids = []
        for (account, month), (expense, refund, categories) in expected.items():
            with self.subTest(account=account, month=month):
                r = approved.query(dict(account_id=account, start_date=f'2026-{month}-01',
                    end_date=f'2026-{month}-'+('31' if month=='08' else '30')), principal_id=approved.PRINCIPAL)
                self.assertEqual(r['bill']['total_expense_minor'], expense)
                self.assertEqual(r['refund_minor'], refund)
                self.assertEqual({c['category']:c['amount_minor'] for c in r['bill']['categories']}, categories)
                ids.extend(t['transaction_id'] for t in r['transactions'])
        self.assertEqual(len(ids), 15)
        self.assertEqual(len(set(ids)), 15)
        hand = reconcile_material(read_workbook(approved.SOURCE))
        self.assertTrue(hand['arithmetic_complete'])
        self.assertFalse([c for c in hand['comparisons'] if c['status'] != 'match'])

    def test_real_material_page_without_bank(self):
        with patch('bank.store.Store.initialize', side_effect=AssertionError('readonly')):
            page = AppTest.from_file(str(approved.ROOT/'app/pages/1_资料账单.py')).run()
            page.button(key='material_query').click().run()
            self.assertFalse(page.exception)
            self.assertEqual(page.metric[0].value,'464.90')

    def test_seed_limits_and_legacy_pending_recovery(self):
        with TemporaryDirectory() as td:
            store = Store(Path(td)/'bank.db'); initialize(store)
            with store.connection(write=True) as c:
                self.assertEqual([tuple(r) for r in c.execute('SELECT card_id,limit_minor FROM cards ORDER BY card_id')],
                    [('CARD-E001',50000),('CARD-E002',20000),('CARD-E003',30000)])
                c.execute('DELETE FROM cards')
                c.execute("INSERT INTO cards VALUES ('sample_card_active','demo_account_a','active',100000,0)")
                c.execute("UPDATE metadata SET value='1' WHERE key='cards_seed_version'")
            g = BankGateway(store)
            req = adapt_request('card.freeze', {'card_id':'sample_card_active'}, request_id='old',trace_id='old',idempotency_key='old')
            pending = g.execute(req,principal_id='demo_user_a')
            initialize(store); g = BankGateway(store)
            n = pending['next_action']
            result = g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],
                decision='approve',proof_token=g.issue_demo_proof(n['challenge_id'],principal_id='demo_user_a')),principal_id='demo_user_a')
            self.assertEqual(result['status'],'succeeded')
            initialize(store)
            self.assertEqual(g.execute(req,principal_id='demo_user_a'),result)
            with store.connection() as c:
                self.assertEqual([tuple(r) for r in c.execute('SELECT card_id,card_state,limit_minor FROM cards')],
                    [('sample_card_active','frozen',100000)])
                self.assertEqual(c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0],200000)
