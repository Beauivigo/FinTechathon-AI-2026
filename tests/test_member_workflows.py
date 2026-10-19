from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from bank.gateway import BankGateway
from bank.store import Store
from shared import adapt_request, ServiceError
from scenario import plan, calendar_dates
import tests.test_merchant as merchant_tests


class MemberWorkflowTests(unittest.TestCase):
    setUp=merchant_tests.MerchantTests.setUp
    req=merchant_tests.MerchantTests.req
    send=merchant_tests.MerchantTests.send
    approve=merchant_tests.MerchantTests.approve
    balance=merchant_tests.MerchantTests.balance

    def reminder(self,when='2027-01-07T09:00:00+08:00'):
        return self.req('subscription.remind',dict(subscription_id='SUB-E003',remind_at=when))

    def cancel(self,sid='SUB-E003'):
        return self.req('subscription.cancel',dict(subscription_id=sid))

    def test_apply_receipt_no_card_and_new_key_pending_rejected(self):
        with self.store.connection(write=True) as c: c.execute("UPDATE cards SET card_state='frozen'")
        req=self.req('card.apply',dict(account_id='demo_account_a'));p=self.send(req)
        self.assertEqual(p['status'],'awaiting_confirmation');done=self.approve(p)
        self.assertEqual(done['status'],'succeeded');self.assertEqual(self.send(req),done)
        self.assertEqual(self.send(self.req('card.apply',dict(account_id='demo_account_a')))['error']['code'],'INVALID_STATE')
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM cards').fetchone()[0],3)
            self.assertEqual(c.execute('SELECT state FROM card_applications').fetchone()[0],'pending')

    def test_apply_active_card_foreign_account_and_rejected_new_application(self):
        self.assertEqual(self.send(self.req('card.apply',dict(account_id='demo_account_a')))['error']['code'],'INVALID_STATE')
        self.assertEqual(self.send(self.req('card.apply',dict(account_id='demo_account_li')))['error']['code'],'FORBIDDEN')
        with self.store.connection(write=True) as c: c.execute("UPDATE cards SET card_state='frozen'")
        self.approve(self.send(self.req('card.apply',dict(account_id='demo_account_a'))),'reject')
        self.assertEqual(self.approve(self.send(self.req('card.apply',dict(account_id='demo_account_a'))))['status'],'succeeded')
        with self.store.connection(write=True) as c: c.execute("UPDATE card_applications SET state='rejected'")  # 明确的模拟审核终态，未实现审核引擎。
        self.assertEqual(self.approve(self.send(self.req('card.apply',dict(account_id='demo_account_a'))))['status'],'succeeded')

    def test_two_apply_confirmations_only_one_pending(self):
        with self.store.connection(write=True) as c: c.execute("UPDATE cards SET card_state='frozen'")
        pending=[self.send(self.req('card.apply',dict(account_id='demo_account_a'))) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool: replies=list(pool.map(self.approve,pending))
        self.assertEqual(sorted(r['status'] for r in replies),['failed','succeeded'])

    def test_reminder_duplicate_keys_and_time_boundaries(self):
        req=self.reminder();done=self.approve(self.send(req));self.assertEqual(self.send(req),done)
        duplicate=self.approve(self.send(self.reminder()));self.assertEqual(duplicate['data'],done['data'])
        self.assertEqual(self.send(self.reminder('2027-01-08T09:00:00+08:00'))['error']['code'],'INVALID_STATE')
        for when in ('2026-10-18T09:00:00+08:00','2027-01-10T00:00:00+08:00','2027-01-11T00:00:00+08:00'):
            self.assertEqual(self.send(self.reminder(when))['error']['code'],'INVALID_REQUEST')
        self.assertEqual(self.balance(),(200000,200000,0))

    def test_reminder_actual_dispatch_restart_and_no_duplicate(self):
        self.approve(self.send(self.reminder()));self.assertEqual(self.g.run_reminders(principal_id='demo_user_a')[0]['state'],'scheduled')
        self.now=datetime(2027,1,7,1,tzinfo=timezone.utc)
        rows=self.g.run_reminders(principal_id='demo_user_a');self.assertEqual(rows[0]['state'],'sent')
        self.g=BankGateway(Store(self.store.path),clock=lambda:self.now);self.g.run_reminders(principal_id='demo_user_a')
        with self.store.connection() as c: self.assertEqual(c.execute("SELECT count(*) FROM audit_events WHERE action='subscription.remind' AND outcome='sent'").fetchone()[0],1)

    def test_cancel_stops_scheduled_reminder_and_clock_advance_no_effects(self):
        self.approve(self.send(self.reminder()));req=self.cancel();done=self.approve(self.send(req))
        self.assertEqual(done['data']['subscription_state'],'cancelled');self.assertEqual(self.send(req),done)
        self.assertEqual(self.send(self.cancel())['error']['code'],'INVALID_STATE')
        for when in (datetime(2027,1,10,tzinfo=timezone.utc),datetime(2028,1,10,tzinfo=timezone.utc),datetime(2029,1,10,tzinfo=timezone.utc)):
            self.now=when;rows=self.g.run_reminders(principal_id='demo_user_a');self.assertEqual(rows[0]['state'],'cancelled')
            self.assertEqual(self.balance(),(200000,200000,0))
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM merchant_orders').fetchone()[0],0)
            self.assertEqual(c.execute("SELECT count(*) FROM audit_events WHERE outcome='sent'").fetchone()[0],0)

    def test_cancel_retains_sent_history(self):
        self.approve(self.send(self.reminder()));self.now=datetime(2027,1,8,tzinfo=timezone.utc)
        self.g.run_reminders(principal_id='demo_user_a');self.approve(self.send(self.cancel()))
        self.assertEqual(self.g.run_reminders(principal_id='demo_user_a')[0]['state'],'sent')

    def test_pending_reminder_cannot_commit_after_cancel(self):
        p=self.send(self.reminder());self.approve(self.send(self.cancel()))
        self.assertEqual(self.approve(p)['status'],'failed')
        with self.store.connection() as c: self.assertEqual(c.execute('SELECT count(*) FROM subscription_reminders').fetchone()[0],0)

    def test_candidate_and_cross_user_cannot_cancel_or_remind(self):
        self.assertEqual(self.send(self.cancel('SUB-E001'))['error']['code'],'INVALID_STATE')
        self.assertEqual(self.g.execute(self.cancel(),principal_id='demo_user_li')['error']['code'],'FORBIDDEN')
        self.assertEqual(self.g.execute(self.reminder(),principal_id='demo_user_li')['error']['code'],'FORBIDDEN')

    def test_cancel_audit_failure_rolls_back_state_and_reminder(self):
        self.approve(self.send(self.reminder()));p=self.send(self.cancel());original=self.g._audit
        def fail(c,r,principal,outcome,now,**details):
            if r['action']=='subscription.cancel' and outcome=='succeeded': raise ServiceError('UPSTREAM_ERROR','模拟审计错误')
            return original(c,r,principal,outcome,now,**details)
        with patch.object(self.g,'_audit',side_effect=fail): self.assertEqual(self.approve(p)['status'],'failed')
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT subscription_state FROM subscriptions WHERE subscription_id='SUB-E003'").fetchone()[0],'active')
            self.assertEqual(c.execute('SELECT state FROM subscription_reminders').fetchone()[0],'scheduled')

    def test_scenario_plan_is_read_only_and_rejects_ambiguous_goal(self):
        req=adapt_request('scenario.plan',dict(goal='生日 2026-11-20'),request_id='plan',trace_id='plan')
        result=self.g.execute(req,principal_id='demo_user_a');self.assertEqual(result['status'],'succeeded')
        self.assertEqual([s['action'] for s in result['data']['steps']],['account.balance','budget.reserve','merchant.order'])
        self.assertEqual(self.balance(),(200000,200000,0))
        with self.store.connection() as c:
            for table in ('operations','reservations','merchant_orders','tasks'): self.assertEqual(c.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        with self.assertRaises(ValueError): plan('随便给我买个礼物',date(2026,10,19))
        self.assertEqual(calendar_dates(date(2026,11,20),date(2026,11,16))['remind_on'],'2026-11-16')
        with self.assertRaises(ValueError): calendar_dates(date(2026,11,20),date(2026,11,17))


if __name__=='__main__': unittest.main()
