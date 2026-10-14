"""仅检查 B0 样例和预期自洽；不实现或证明 bill.analyze 已可调用。"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from shared import adapt_request
from shared.contracts import check_value, ENUMS

FIXTURE_PATH = Path(__file__).parent / 'fixtures' / 'billing_b0.json'


class BillingFixtureTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
        self.rows = self.data['transactions']

    def test_rows_obey_existing_contract_and_cover_all_kinds(self):
        self.assertIs(self.data['simulated'], True)
        self.assertEqual(self.data['amount_unit'], 'integer_fen')
        check_value(self.rows, 'list:transaction', 'fixture_transactions')
        self.assertEqual(len({r['transaction_id'] for r in self.rows}), len(self.rows))
        self.assertEqual({r['kind'] for r in self.rows}, ENUMS['transaction_kind'])
        accounts = {a['account_id']: a for a in self.data['accounts']}
        self.assertEqual(len(accounts), len(self.data['accounts']))
        for account in accounts.values():
            check_value(account['account_id'], 'id', 'account_id')
            check_value(account['principal_id'], 'id', 'principal_id')
        for row in self.rows:
            self.assertIn(row['account_id'], accounts)
            self.assertIsNotNone(datetime.fromisoformat(row['occurred_at'].replace('Z', '+00:00')).utcoffset())

    def test_opening_plus_history_matches_closing_and_transfer_is_paired(self):
        for account in self.data['accounts']:
            balance = account['opening_balance_minor']
            check_value(balance, 'nonnegative', 'opening')
            rows = sorted((r for r in self.rows if r['account_id'] == account['account_id']),
                key=lambda r: datetime.fromisoformat(r['occurred_at'].replace('Z', '+00:00')))
            for row in rows:
                balance += row['amount_minor'] * (1 if row['direction'] == 'credit' else -1)
                check_value(balance, 'nonnegative', 'running_balance')
            self.assertEqual(balance, account['closing_balance_minor'])
        pair = [r for r in self.rows if r['transaction_id'] in self.data['paired_transfer_ids']]
        self.assertEqual(len(pair), 2)
        self.assertEqual({r['direction'] for r in pair}, {'credit', 'debit'})
        self.assertEqual({r['kind'] for r in pair}, {'transfer'})
        self.assertEqual(pair[0]['amount_minor'], pair[1]['amount_minor'])
        self.assertEqual(pair[0]['occurred_at'], pair[1]['occurred_at'])

    def test_explicit_hand_answers_match_listed_rows_and_date_boundaries(self):
        # 校对静态答案；后续 B1/B2 必须对实际模块/网关另做结果测试。
        for case in self.data['cases']:
            with self.subTest(case=case['case_id']):
                p = case['params']
                adapt_request('bill.analyze', p, request_id='fixture', trace_id='fixture')
                owner = next(a['principal_id'] for a in self.data['accounts'] if a['account_id'] == p['account_id'])
                self.assertEqual(owner, case['principal_id'])
                rows = []
                for row in self.rows:
                    day = datetime.fromisoformat(row['occurred_at'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=8))).date().isoformat()
                    if row['account_id'] == p['account_id'] and p['start_date'] <= day <= p['end_date']:
                        rows.append(row)
                expense = [r for r in rows if r['direction'] == 'debit' and r['kind'] in ('consumption', 'subscription')]
                refund = [r for r in rows if r['direction'] == 'credit' and r['kind'] == 'refund']
                self.assertEqual({r['transaction_id'] for r in expense}, set(case['included_transaction_ids']))
                self.assertEqual(sum(r['amount_minor'] for r in expense), case['total_expense_minor'])
                categories = {c['category']: c['amount_minor'] for c in case['categories']}
                self.assertEqual(len(categories), len(case['categories']))
                self.assertEqual(set(categories), {r['category'] for r in expense})
                for category, amount in categories.items():
                    self.assertEqual(sum(r['amount_minor'] for r in expense if r['category'] == category), amount)
                self.assertEqual(sum(categories.values()), case['total_expense_minor'])
                self.assertEqual({r['transaction_id'] for r in refund}, set(case['refund_transaction_ids']))
                self.assertEqual(sum(r['amount_minor'] for r in refund), case['refund_minor'])

    def test_forbidden_case_really_targets_another_owner(self):
        case = self.data['forbidden_case']
        owner = next(a['principal_id'] for a in self.data['accounts'] if a['account_id'] == case['params']['account_id'])
        self.assertNotEqual(case['principal_id'], owner)
        self.assertEqual(case['expected_code'], 'FORBIDDEN')
        adapt_request('bill.analyze', case['params'], request_id='forbidden_fixture', trace_id='fixture')


if __name__ == '__main__':
    unittest.main()
