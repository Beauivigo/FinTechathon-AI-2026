"""D 赎回验收：锁定边界、份额、费用、清仓与幂等。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import adapt_request
from shared.contracts import validate_result

LETTERS = {1: "a", 2: "b", 3: "c", 4: "d"}


def answers(scores):
    return {f"q{i}": f"q{i}_{LETTERS[score]}" for i, score in enumerate(scores, 1)}


class WealthRedeemTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / "redeem.sqlite3")
        initialize(self.store)
        self.now = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
        self.g = BankGateway(self.store, clock=lambda: self.now)

    def assess(self, scores):
        request = adapt_request("wealth.assess", {"questionnaire_version": "wpq_v1", "answers": answers(scores)},
                                request_id="a_" + uuid4().hex, trace_id="t", idempotency_key="a_" + uuid4().hex)
        return self.g.execute(request, principal_id="demo_user_a")["data"]["risk_profile_id"]

    def verify(self, pending, proof=None):
        target = pending["next_action"]
        token = proof or self.g.issue_demo_proof(target["challenge_id"], principal_id="demo_user_a")
        return self.g.authorize(dict(schema_version="1.1", challenge_id=target["challenge_id"],
                                     operation_digest=target["operation_digest"],
                                     decision="approve", proof_token=token),
                                principal_id="demo_user_a")

    def buy(self, scores, product, amount):
        profile = self.assess(scores)
        request = adapt_request("wealth.purchase",
                                dict(account_id="demo_account_a", product_id=product,
                                     risk_profile_id=profile, amount_minor=amount),
                                request_id="p_" + uuid4().hex, trace_id="t", idempotency_key="p_" + uuid4().hex)
        pending = self.g.execute(request, principal_id="demo_user_a")
        result = self.verify(pending)
        self.assertEqual(result["status"], "succeeded", result)
        with self.store.connection() as c:
            return c.execute("SELECT * FROM holdings WHERE product_id=?", (product,)).fetchone()

    def redeem(self, holding_id, units, key=None):
        request = adapt_request("wealth.redeem",
                                dict(account_id="demo_account_a", holding_id=holding_id, units=units),
                                request_id="r_" + uuid4().hex, trace_id="t",
                                idempotency_key=key or "r_" + uuid4().hex)
        return request, self.g.execute(request, principal_id="demo_user_a")

    def holding(self, holding_id):
        with self.store.connection() as c:
            return c.execute("SELECT * FROM holdings WHERE holding_id=?", (holding_id,)).fetchone()

    def balance(self):
        with self.store.connection() as c:
            return c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0]

    def test_lock_boundary_and_partial_redeem(self):
        holding = self.buy((3, 3, 3, 2, 2), "WP-C02", 50000)  # R3, lock 7, units 499.5000
        self.assertEqual(holding["units"], "499.5000")

        _, early = self.redeem(holding["holding_id"], "100.0000")
        self.assertEqual(early["error"]["code"], "INVALID_STATE")

        self.now += timedelta(days=7)  # 10-26，解锁当日
        request, pending = self.redeem(holding["holding_id"], "200.0000")
        self.assertEqual(pending["status"], "awaiting_verification")
        result = self.verify(pending)
        self.assertEqual(result["status"], "succeeded", result)
        validate_result(request, result)
        self.assertEqual(result["data"]["units"], "200")
        self.assertEqual(self.holding(holding["holding_id"])["units"], "299.5000")
        self.assertEqual(self.balance(), 170000)

    def test_redeem_fee_and_clear_position(self):
        holding = self.buy((4, 4, 3, 3, 3), "WP-C04", 100000)  # R4, lock 90, 495.0000
        self.now += timedelta(days=90)
        request, pending = self.redeem(holding["holding_id"], "200.0000")
        result = self.verify(pending)
        self.assertEqual(result["status"], "succeeded")
        # 毛 200×200=40000，费 0.5%=200，到账 39800
        self.assertEqual(self.balance(), 100000 + 39800)
        self.assertEqual(self.holding(holding["holding_id"])["units"], "295.0000")

        _, rest = self.redeem(holding["holding_id"], "295.0000")
        cleared = self.verify(rest)
        self.assertEqual(cleared["status"], "succeeded")
        row = self.holding(holding["holding_id"])
        self.assertEqual(row["units"], "0.0000")
        self.assertEqual(row["state"], "closed")
        _, again = self.redeem(holding["holding_id"], "1.0000")
        self.assertEqual(again["error"]["code"], "INVALID_STATE")

    def test_units_above_holding_rejected_before_confirmation(self):
        holding = self.buy((3, 3, 3, 2, 2), "WP-C02", 50000)
        self.now += timedelta(days=7)
        _, too_many = self.redeem(holding["holding_id"], "500.0000")
        self.assertEqual(too_many["status"], "failed")
        self.assertEqual(too_many["error"]["code"], "INVALID_STATE")
        self.assertEqual(self.balance(), 150000)

    def test_same_key_replays(self):
        holding = self.buy((3, 3, 3, 2, 2), "WP-C02", 50000)
        self.now += timedelta(days=7)
        request, pending = self.redeem(holding["holding_id"], "100.0000", key="fixed_redeem")
        first = self.verify(pending)
        self.assertEqual(self.g.execute(request, principal_id="demo_user_a"), first)
        self.assertEqual(self.holding(holding["holding_id"])["units"], "399.5000")


if __name__ == "__main__":
    unittest.main()
