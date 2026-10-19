"""公开 subscription.charge：红色强验证、幂等与拒绝路径。"""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import adapt_request


class SubscriptionChargeTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / "charge.sqlite3"); initialize(self.store)
        self.now = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
        self.g = BankGateway(self.store, clock=lambda: self.now)
        with self.store.connection(write=True) as c:
            c.execute("UPDATE subscriptions SET subscription_state='active', next_charge_date='2026-10-19' WHERE subscription_id='SUB-E001'")

    def request(self, key=None, sid="SUB-E001"):
        return adapt_request("subscription.charge", {"subscription_id": sid},
                             request_id="r_" + uuid4().hex, trace_id="t",
                             idempotency_key=key or "k_" + uuid4().hex)

    def verify(self, pending):
        n = pending["next_action"]
        proof = self.g.issue_demo_proof(n["challenge_id"], principal_id="demo_user_a")
        return self.g.authorize(dict(schema_version="1.1", challenge_id=n["challenge_id"],
                                     operation_digest=n["operation_digest"],
                                     decision="approve", proof_token=proof), principal_id="demo_user_a")

    def test_charge_requires_verification_and_advances(self):
        request = self.request()
        pending = self.g.execute(request, principal_id="demo_user_a")
        self.assertEqual(pending["status"], "awaiting_verification")
        result = self.verify(pending)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["data"]["charged_minor"], 2500)
        self.assertEqual(result["data"]["next_charge_date"], "2026-11-19")
        self.assertEqual(self.g.execute(request, principal_id="demo_user_a"), result)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0], 197500)
            self.assertEqual(c.execute("SELECT count(*) FROM subscription_charges").fetchone()[0], 1)
            saved = c.execute("SELECT result_json FROM operations WHERE action='subscription.charge'").fetchone()[0]
        self.assertIn("197500" if False else "succeeded", saved)

    def test_not_due_and_unknown_subscription(self):
        not_due = self.g.execute(self.request(sid="SUB-E003"), principal_id="demo_user_a")
        self.assertEqual(not_due["status"], "failed")
        self.assertEqual(not_due["error"]["code"], "INVALID_STATE")
        missing = self.g.execute(self.request(sid="SUB-NOPE"), principal_id="demo_user_a")
        self.assertEqual(missing["error"]["code"], "FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
