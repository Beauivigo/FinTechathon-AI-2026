"""E-03 自动识别验收：周期、容差、去重、权限与只读性。"""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store, utc_timestamp
from shared import adapt_request


class SubscriptionScanTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'scan.db')
        initialize(self.store)
        self.now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        self.g = BankGateway(self.store, clock=lambda: self.now)

    def add(self, merchant, day, amount, *, account='demo_account_a', kind='consumption',
            direction='debit', tid=None):
        tid = tid or f'{merchant}-{day}-{amount}'
        with self.store.connection(write=True) as c:
            c.execute('''INSERT INTO ledger_entries
                (transaction_id,account_id,reference_id,posting_key,amount_minor,direction,kind,
                 category,occurred_at,merchant_name) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (tid, account, tid, tid, amount, direction, kind, '自动识别',
                 utc_timestamp(day + 'T04:00:00+00:00'), merchant))

    def request(self, account='demo_account_a', key=None):
        return adapt_request('subscription.scan', dict(account_id=account),
                             request_id='req_' + uuid4().hex, trace_id='t',
                             idempotency_key=key or 'scan_' + uuid4().hex)

    def finish(self, pending, decision='approve'):
        target = pending['next_action']
        return self.g.authorize(dict(schema_version='1.1', challenge_id=target['challenge_id'],
                                     operation_digest=target['operation_digest'], decision=decision),
                                principal_id='demo_user_a')

    def scan(self, account='demo_account_a', key=None):
        pending = self.g.execute(self.request(account, key), principal_id='demo_user_a')
        self.assertEqual(pending['status'], 'awaiting_confirmation', pending)
        return self.finish(pending)

    def test_monthly_recognition_creates_candidate(self):
        for day in ('2026-08-05', '2026-09-05', '2026-10-05'):
            self.add('自动识别商户甲', day, 2500)
        result = self.scan()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['data']['created'], 1)
        sub = result['data']['subscriptions'][0]
        self.assertEqual(sub['merchant_name'], '自动识别商户甲')
        self.assertEqual(sub['interval'], 'monthly')
        self.assertEqual(sub['next_charge_date'], '2026-11-05')
        self.assertEqual(sub['subscription_state'], 'candidate')
        self.assertIn('3 期', sub['evidence'])
        listed = self.finish(self.g.execute(
            adapt_request('subscription.list', dict(account_id='demo_account_a'),
                          request_id='list', trace_id='t'), principal_id='demo_user_a'))
        self.assertIn(sub['subscription_id'], [row['subscription_id'] for row in listed['data']['subscriptions']])

    def test_single_period_is_not_recognized(self):
        self.add('只扣一次', '2026-09-05', 2500)
        self.assertEqual(self.scan()['data']['created'], 0)

    def test_period_deviation_three_days_passes_four_days_fails(self):
        self.add('偏差三天', '2026-08-05', 2500)
        self.add('偏差三天', '2026-09-08', 2500)
        self.assertEqual(self.scan()['data']['created'], 1)
        self.add('偏差四天', '2026-08-05', 2500, tid='偏差四天-1')
        self.add('偏差四天', '2026-09-09', 2500, tid='偏差四天-2')
        self.assertEqual(self.scan()['data']['created'], 0)

    def test_amount_tolerance_boundary(self):
        self.add('容差通过', '2026-08-05', 6800)
        self.add('容差通过', '2026-09-05', 7140)
        self.assertEqual(self.scan()['data']['created'], 1)
        self.add('容差拒绝', '2026-08-05', 6800, tid='容差拒绝-1')
        self.add('容差拒绝', '2026-09-05', 7141, tid='容差拒绝-2')
        self.assertEqual(self.scan()['data']['created'], 0)

    def test_month_end_anchor_keeps_day(self):
        for day in ('2026-08-31', '2026-09-30', '2026-10-31'):
            self.add('月末商户', day, 3000)
        result = self.scan()
        self.assertEqual(result['data']['created'], 1)
        self.assertEqual(result['data']['subscriptions'][0]['interval'], 'monthly')
        self.assertEqual(result['data']['subscriptions'][0]['next_charge_date'], '2026-11-30')

    def test_yearly_and_leap_day(self):
        self.add('年付商户', '2025-03-15', 12000)
        self.add('年付商户', '2026-03-15', 12000)
        result = self.scan()
        self.assertEqual(result['data']['created'], 1)
        self.assertEqual(result['data']['subscriptions'][0]['interval'], 'yearly')
        self.assertEqual(result['data']['subscriptions'][0]['next_charge_date'], '2027-03-15')
        self.add('闰日商户', '2024-02-29', 6000, tid='leap-1')
        self.add('闰日商户', '2025-02-28', 6000, tid='leap-2')
        self.assertEqual(self.scan()['data']['created'], 1)

    def test_existing_subscription_and_rescan_do_not_duplicate(self):
        for day in ('2026-08-05', '2026-09-05', '2026-10-05'):
            self.add('模拟影音会员', day, 2500)
        self.assertEqual(self.scan()['data']['created'], 0)
        self.add('重复扫描商户', '2026-08-06', 1000)
        self.add('重复扫描商户', '2026-09-06', 1000)
        self.assertEqual(self.scan()['data']['created'], 1)
        self.assertEqual(self.scan()['data']['created'], 0)

    def test_same_key_replays_without_second_scan(self):
        self.add('幂等商户', '2026-08-07', 900)
        self.add('幂等商户', '2026-09-07', 900)
        request = self.request(key='fixed_key_1')
        first = self.finish(self.g.execute(request, principal_id='demo_user_a'))
        second = self.g.execute(request, principal_id='demo_user_a')
        self.assertEqual(first, second)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM subscriptions WHERE merchant_name='幂等商户'").fetchone()[0], 1)

    def test_credit_and_refund_rows_are_ignored(self):
        for day in ('2026-08-08', '2026-09-08'):
            self.add('入账不算', day, 1000, kind='refund', direction='credit')
        self.assertEqual(self.scan()['data']['created'], 0)

    def test_account_scope_and_ownership(self):
        self.add('别家商户', '2026-08-09', 800, account='demo_account_li')
        self.add('别家商户', '2026-09-09', 800, account='demo_account_li')
        self.assertEqual(self.scan()['data']['created'], 0)
        denied = self.g.execute(self.request('demo_account_li'), principal_id='demo_user_a')
        self.assertEqual(denied['status'], 'failed')
        self.assertEqual(denied['error']['code'], 'FORBIDDEN')

    def test_scan_does_not_touch_balance(self):
        self.add('只读校验', '2026-08-10', 1200)
        self.add('只读校验', '2026-09-10', 1200)
        with self.store.connection() as c:
            before = [tuple(row) for row in c.execute('SELECT account_id,balance_minor,available_minor,reserved_minor FROM accounts ORDER BY account_id')]
        result = self.scan()
        self.assertEqual(result['data']['created'], 1)
        with self.store.connection() as c:
            after = [tuple(row) for row in c.execute('SELECT account_id,balance_minor,available_minor,reserved_minor FROM accounts ORDER BY account_id')]
        self.assertEqual(before, after)

    def test_bad_account_and_reject_leave_no_candidate(self):
        self.add('拒绝商户', '2026-08-11', 700)
        self.add('拒绝商户', '2026-09-11', 700)
        pending = self.g.execute(self.request(), principal_id='demo_user_a')
        self.assertEqual(pending['status'], 'awaiting_confirmation')
        rejected = self.finish(pending, 'reject')
        self.assertEqual(rejected['status'], 'cancelled')
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM subscriptions WHERE merchant_name='拒绝商户'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
