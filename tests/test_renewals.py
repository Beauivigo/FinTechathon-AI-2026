"""半自动续费验收：到期扣款、日期推进、幂等与余额不足。"""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bank import renewals
from bank.seed import initialize
from bank.store import Store
from shared import ServiceError


class RenewalTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / "renew.sqlite3")
        initialize(self.store)
        self.now = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
        with self.store.connection(write=True) as c:
            c.execute("UPDATE subscriptions SET subscription_state='active', next_charge_date='2026-10-19' WHERE subscription_id='SUB-E001'")

    def charge(self, sid="SUB-E001", key=None):
        with self.store.connection(write=True) as c:
            return renewals.charge(self.store, c, sid, "demo_user_a", self.now, key or "k-" + sid)

    def state(self):
        with self.store.connection() as c:
            account = c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0]
            sub = c.execute("SELECT next_charge_date FROM subscriptions WHERE subscription_id='SUB-E001'").fetchone()[0]
            ledger = c.execute("SELECT count(*) FROM ledger_entries WHERE kind='subscription'").fetchone()[0]
        return account, sub, ledger

    def test_due_charge_advances_and_replays(self):
        first = self.charge()
        self.assertEqual(first["charged_minor"], 2500)
        self.assertEqual(first["next_charge_date"], "2026-11-19")
        self.assertEqual(self.state(), (197500, "2026-11-19", 1))
        second = self.charge()
        self.assertTrue(second["replayed"])
        self.assertEqual(self.state(), (197500, "2026-11-19", 1))

    def test_not_due_and_not_active(self):
        with self.assertRaises(ServiceError) as caught:
            self.charge("SUB-E003")
        self.assertEqual(caught.exception.code, "INVALID_STATE")
        with self.store.connection(write=True) as c:
            c.execute("UPDATE subscriptions SET next_charge_date='2026-10-19', subscription_state='candidate' WHERE subscription_id='SUB-E001'")
        with self.assertRaises(ServiceError) as caught:
            self.charge()
        self.assertEqual(caught.exception.code, "INVALID_STATE")

    def test_insufficient_funds_leaves_no_trace(self):
        with self.store.connection(write=True) as c:
            c.execute("UPDATE subscriptions SET amount_minor=999999 WHERE subscription_id='SUB-E001'")
        with self.assertRaises(ServiceError) as caught:
            self.charge()
        self.assertEqual(caught.exception.code, "INSUFFICIENT_FUNDS")
        self.assertEqual(self.state()[0], 200000)


if __name__ == "__main__":
    unittest.main()
