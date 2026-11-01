"""review独立资金复核：临时库，回归实际规则缺口。"""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import unittest

from shared import adapt_request, ServiceError
from tests import test_subscription_charge as helpers
from tests import test_wealth_redeem as wealth_helpers
from shared.contracts import MAX_SAFE_INTEGER


class RenewalReviewTests(unittest.TestCase):
    setUp=helpers.SubscriptionChargeTests.setUp
    request=helpers.SubscriptionChargeTests.request
    verify=helpers.SubscriptionChargeTests.verify

    def snapshot(self):
        with self.store.connection() as c:
            return {t:[tuple(r) for r in c.execute('SELECT * FROM '+t)]
                    for t in ('accounts','ledger_entries','subscription_charges','subscriptions')}

    def test_frozen_lost_or_low_limit_card_blocks_charge_before_verification(self):
        for state,limit,expected in [('frozen',100000,'INVALID_STATE'),('lost',100000,'INVALID_STATE'),('active',1,'LIMIT_EXCEEDED')]:
            with self.subTest(state=state,limit=limit):
                with self.store.connection(write=True) as c:
                    c.execute('UPDATE cards SET card_state=?,limit_minor=?,revision=revision+1 WHERE account_id=?',(state,limit,'demo_account_a'))
                before=self.snapshot()
                result=self.g.execute(self.request(),principal_id='demo_user_a')
                self.assertEqual(result['status'],'failed')
                self.assertEqual(result['error']['code'],expected)
                self.assertEqual(self.snapshot(),before)

    def test_card_change_during_challenge_cannot_silently_switch_card(self):
        pending=self.g.execute(self.request(),principal_id='demo_user_a')
        with self.store.connection(write=True) as c:
            c.execute("INSERT INTO cards VALUES ('ZZ-NEW','demo_account_a','active',100000,0)")
            c.execute("UPDATE cards SET card_state='frozen',revision=revision+1 WHERE card_id='CARD-E001'")
        before=self.snapshot()
        result=self.verify(pending)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(self.snapshot(),before)

    def test_account_rebinding_during_challenge_invalidates_old_authorization(self):
        pending=self.g.execute(self.request(),principal_id='demo_user_a')
        with self.store.connection(write=True) as c:
            c.execute("INSERT INTO accounts VALUES ('demo_account_a_2','demo_user_a','模拟备用账户','CNY',200000,200000,200000,0)")
            c.execute("INSERT INTO cards VALUES ('OTHER-CARD','demo_account_a_2','active',100000,0)")
            c.execute("UPDATE subscriptions SET account_id='demo_account_a_2' WHERE subscription_id='SUB-E001'")
        before=self.snapshot();result=self.verify(pending)
        self.assertEqual(result['status'],'failed');self.assertEqual(self.snapshot(),before)

    def test_same_period_concurrency_commits_exactly_once(self):
        requests=[self.request(),self.request()]
        pending=[self.g.execute(r,principal_id='demo_user_a') for r in requests]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(self.verify,pending))
        self.assertEqual(sum(r['status']=='succeeded' for r in results),1)
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM subscription_charges').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM ledger_entries').fetchone()[0],1)
            self.assertEqual(c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0],197500)

    def test_cancel_before_authorization_stops_charge(self):
        pending=self.g.execute(self.request(),principal_id='demo_user_a')
        cancel=adapt_request('subscription.cancel',{'subscription_id':'SUB-E001'},request_id='cancel',trace_id='cancel',idempotency_key='cancel')
        result=self.g.execute(cancel,principal_id='demo_user_a');n=result['next_action']
        self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve'),principal_id='demo_user_a')
        before=self.snapshot();self.assertEqual(self.verify(pending)['status'],'failed');self.assertEqual(self.snapshot(),before)

    def test_charge_audit_failure_rolls_back_every_business_write(self):
        pending=self.g.execute(self.request(),principal_id='demo_user_a');before=self.snapshot();original=self.g._audit
        def broken(c,request,principal,outcome,now,**details):
            if request['action']=='subscription.charge' and outcome=='succeeded':
                raise ServiceError('UPSTREAM_ERROR','模拟审计故障')
            return original(c,request,principal,outcome,now,**details)
        with patch.object(self.g,'_audit',side_effect=broken):self.assertEqual(self.verify(pending)['status'],'failed')
        self.assertEqual(self.snapshot(),before)

    def test_monthly_anchor_survives_short_month(self):
        from datetime import datetime,timezone
        with self.store.connection(write=True) as c:
            c.execute("UPDATE subscriptions SET next_charge_date='2027-01-31' WHERE subscription_id='SUB-E001'")
        self.now=datetime(2027,1,31,4,tzinfo=timezone.utc)
        self.assertEqual(self.verify(self.g.execute(self.request(),principal_id='demo_user_a'))['data']['next_charge_date'],'2027-02-28')
        self.now=datetime(2027,2,28,4,tzinfo=timezone.utc)
        self.assertEqual(self.verify(self.g.execute(self.request(),principal_id='demo_user_a'))['data']['next_charge_date'],'2027-03-31')

    def test_yearly_anchor_returns_to_leap_day(self):
        from datetime import datetime,timezone
        with self.store.connection(write=True) as c:
            c.execute("UPDATE subscriptions SET next_charge_date='2024-02-29',interval='yearly' WHERE subscription_id='SUB-E001'")
        for year in range(2024,2028):
            day=29 if year==2024 else 28
            self.now=datetime(year,2,day,4,tzinfo=timezone.utc)
            result=self.verify(self.g.execute(self.request(),principal_id='demo_user_a'))
        self.assertEqual(result['data']['next_charge_date'],'2028-02-29')


class RedemptionReviewTests(unittest.TestCase):
    setUp=wealth_helpers.WealthRedeemTests.setUp
    assess=wealth_helpers.WealthRedeemTests.assess
    verify=wealth_helpers.WealthRedeemTests.verify
    buy=wealth_helpers.WealthRedeemTests.buy
    redeem=wealth_helpers.WealthRedeemTests.redeem

    def test_same_key_is_independent_between_purchase_and_redeem(self):
        profile=self.assess((1,1,1,1,1))
        request=adapt_request('wealth.purchase',dict(account_id='demo_account_a',product_id='WP-C01',
            risk_profile_id=profile,amount_minor=10000),request_id='purchase',trace_id='scope',idempotency_key='same-key')
        self.assertEqual(self.verify(self.g.execute(request,principal_id='demo_user_a'))['status'],'succeeded')
        with self.store.connection() as c:holding=c.execute('SELECT holding_id FROM holdings').fetchone()[0]
        _,pending=self.redeem(holding,'10.0000',key='same-key')
        self.assertEqual(self.verify(pending)['status'],'succeeded')

    def test_v12_order_migration_preserves_order_and_money(self):
        from bank.store import Store,WEALTH_SCHEMA,STORAGE_VERSION
        import sqlite3
        holding=self.buy((1,1,1,1,1),'WP-C01',10000)
        with self.store.connection(write=True) as c:
            before=[tuple(r) for r in c.execute('SELECT * FROM wealth_orders')]
            balances=[tuple(r) for r in c.execute('SELECT * FROM accounts')]
            c.execute('DROP TABLE wealth_orders')
            old=WEALTH_SCHEMA[-1].replace('UNIQUE(principal_id, order_type, operation_key)','UNIQUE(principal_id, operation_key)')
            c.execute(old)
            c.executemany('INSERT INTO wealth_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',before)
            c.execute('PRAGMA user_version=12')
        Store(self.store.path).initialize()
        with self.store.connection() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],STORAGE_VERSION)
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM wealth_orders')],before)
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM accounts')],balances)
            self.assertIn('UNIQUE(principal_id, order_type, operation_key)',c.execute("SELECT sql FROM sqlite_master WHERE name='wealth_orders'").fetchone()[0])

    def test_zero_cent_redemption_rejected_before_challenge(self):
        holding=self.buy((1,1,1,1,1),'WP-C01',10000)
        _,result=self.redeem(holding['holding_id'],'0.0001')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['code'],'LIMIT_EXCEEDED')

    def test_redemption_balance_limit_checked_before_challenge(self):
        holding=self.buy((1,1,1,1,1),'WP-C01',10000)
        with self.store.connection(write=True) as c:
            c.execute("UPDATE accounts SET balance_minor=?,available_minor=? WHERE account_id='demo_account_a'",(MAX_SAFE_INTEGER,MAX_SAFE_INTEGER))
        _,result=self.redeem(holding['holding_id'],'1.0000')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['code'],'LIMIT_EXCEEDED')

