"""B2：仅在临时库装载虚构历史，测试网关权限、快照与只读性。"""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store, utc_timestamp
from billing import analyze
from shared import adapt_request, validate_result, ServiceError
from shared.contracts import MAX_SAFE_INTEGER


class BillingGatewayTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'billing.sqlite3')
        initialize(self.store)
        self.fixture = json.loads((Path(__file__).parent / 'fixtures/billing_b0.json').read_text(encoding='utf-8'))
        with self.store.connection(write=True) as c:
            for owner in {a['principal_id'] for a in self.fixture['accounts']}:
                c.execute('INSERT INTO principals VALUES (?,?)', (owner, 'B0 虚构用户'))
            for a in self.fixture['accounts']:
                opening = a['opening_balance_minor']
                c.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)',
                          (a['account_id'], a['principal_id'], 'B0 虚构账户', 'CNY', opening, opening, opening, 0))
            for row in self.fixture['transactions']:
                self.insert(c, row)
            for a in self.fixture['accounts']:
                self.assertEqual(c.execute('SELECT balance_minor FROM accounts WHERE account_id=?',
                                           (a['account_id'],)).fetchone()[0], a['closing_balance_minor'])
        self.gateway = BankGateway(self.store)
        self.params = dict(account_id='b0_account_a', start_date='2026-08-01', end_date='2026-08-31')

    @staticmethod
    def insert(c, row):
        # 测试专用已记账历史；同步余额，不能作为公开充值或扣款入口。
        key = row['transaction_id']
        c.execute('''INSERT INTO ledger_entries (transaction_id,account_id,reference_id,posting_key,
            amount_minor,direction,kind,category,occurred_at) VALUES (?,?,?,?,?,?,?,?,?)''',
            (key, row['account_id'], key, key, row['amount_minor'], row['direction'], row['kind'],
             row['category'], utc_timestamp(row['occurred_at'])))
        delta = row['amount_minor'] * (1 if row['direction'] == 'credit' else -1)
        c.execute('UPDATE accounts SET balance_minor=balance_minor+?,available_minor=available_minor+? WHERE account_id=?',
                  (delta, delta, row['account_id']))

    def run_query(self, params=None, principal='b0_user_a'):
        request = adapt_request('bill.analyze', params or self.params, request_id='b2_request', trace_id='b2_trace')
        result = self.gateway.execute(request, principal_id=principal)
        validate_result(request, result)
        return result

    def dump(self):
        with self.store.connection() as c:
            return '\n'.join(c.iterdump())

    def test_fixture_answers_through_gateway(self):
        before = self.dump()
        for case in self.fixture['cases']:
            with self.subTest(case=case['case_id']):
                result = self.run_query(case['params'], case['principal_id'])
                self.assertEqual(result['status'], 'succeeded')
                self.assertEqual(result['data']['total_expense_minor'], case['total_expense_minor'])
                self.assertEqual(result['data']['categories'], sorted(case['categories'], key=lambda r: r['category']))
                self.assertIn('自动异常检测尚未启用', result['data']['report'])
        self.assertEqual(before, self.dump())

    def test_forbidden_and_unknown_identities_no_data_leak(self):
        for principal, account in [('b0_user_a','b0_account_b'), ('missing','b0_account_a'),
                                   (None,'b0_account_a'), ('b0_user_a','missing')]:
            result = self.run_query(dict(self.params, account_id=account), principal)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error']['code'], 'FORBIDDEN')
            self.assertEqual(result['data'], {})

    def test_more_than_one_hundred_and_query_list_date_parity(self):
        with self.store.connection(write=True) as c:
            for i in range(251):
                self.insert(c, dict(self.fixture['transactions'][0], transaction_id=f'many_{i}', amount_minor=1))
        self.assertEqual(self.run_query()['data']['total_expense_minor'], 12951)
        rows, params = [], dict(self.params, limit=100)
        while True:
            request = adapt_request('transaction.list', params, request_id='list', trace_id='list')
            result = self.gateway.execute(request, principal_id='b0_user_a')
            validate_result(request, result)
            rows.extend(result['data']['transactions'])
            if not result['data']['next_cursor']:
                break
            params['cursor'] = result['data']['next_cursor']
        self.assertGreater(len(rows), 251)
        self.assertEqual(self.run_query()['data'], analyze(self.params, rows))

    def test_concurrent_committed_insert_appears_only_next_query(self):
        # WAL 允许真实写入在读快照期间提交；不用 sleep 或时序碰运气。
        with closing(sqlite3.connect(self.store.path)) as c:
            c.execute('PRAGMA journal_mode=WAL')
        def during_read(params, rows):
            with self.store.connection(write=True) as c:
                self.insert(c, dict(self.fixture['transactions'][0], transaction_id='concurrent', amount_minor=123))
            return analyze(params, rows)
        with patch('bank.services.analyze', side_effect=during_read):
            result = self.run_query()
        self.assertEqual(result['data']['total_expense_minor'], 12700)
        self.assertEqual(self.run_query()['data']['total_expense_minor'], 12823)

    def test_pending_authorization_and_entire_database_unchanged(self):
        request = adapt_request('transfer.create', dict(account_id='demo_account_a', payee_id='demo_payee_li',
            amount_minor=20000), request_id='pending', trace_id='pending', idempotency_key='pending')
        pending = self.gateway.execute(request, principal_id='demo_user_a')
        self.assertEqual(pending['status'], 'awaiting_confirmation')
        before = self.dump()
        self.run_query()
        self.run_query(dict(self.params, account_id='b0_account_b'))
        self.assertEqual(before, self.dump())

    def test_bad_ledger_and_database_failure_are_failed(self):
        with self.store.connection(write=True) as c:
            c.execute("UPDATE ledger_entries SET category='' WHERE transaction_id='b0_tx_01'")
        self.assertEqual(self.run_query()['error']['code'], 'UPSTREAM_ERROR')
        with patch.object(self.store, '_connect', side_effect=sqlite3.OperationalError('unavailable')):
            result = self.run_query()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'UPSTREAM_ERROR')

    def test_bad_backend_receipt_is_not_success(self):
        with patch('bank.services.analyze', return_value=dict(total_expense_minor=1, categories=[], anomalies=[], report='bad')):
            result = self.run_query()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'UPSTREAM_ERROR')

    def test_invalid_dates_rejected_before_read(self):
        request = adapt_request('bill.analyze', self.params, request_id='invalid', trace_id='invalid')
        request = deepcopy(request)
        request['params']['start_date'] = '2026-02-30'
        with patch.object(self.gateway.services, 'query') as query:
            with self.assertRaises(ServiceError) as error:
                self.gateway.execute(request, principal_id='b0_user_a')
        self.assertEqual(error.exception.code, 'INVALID_REQUEST')
        query.assert_not_called()

    def test_aggregate_overflow_is_limit_exceeded(self):
        # 腐坏/极端历史注入，仅测试拒绝，不作为合法资金样例。
        with self.store.connection(write=True) as c:
            c.execute("UPDATE ledger_entries SET amount_minor=? WHERE transaction_id='b0_tx_01'", (MAX_SAFE_INTEGER,))
        result = self.run_query()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'LIMIT_EXCEEDED')


if __name__ == '__main__':
    unittest.main()
