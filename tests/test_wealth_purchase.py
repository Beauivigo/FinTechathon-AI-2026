"""D 申购验收：风险/起购/余额拦截、费用份额、红色流程与幂等。"""

from datetime import datetime, timezone
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


class WealthPurchaseTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / "purchase.sqlite3")
        initialize(self.store)
        self.now = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
        self.g = BankGateway(self.store, clock=lambda: self.now)

    def assess(self, scores):
        request = adapt_request("wealth.assess", {"questionnaire_version": "wpq_v1", "answers": answers(scores)},
                                request_id="a_" + uuid4().hex, trace_id="t", idempotency_key="a_" + uuid4().hex)
        result = self.g.execute(request, principal_id="demo_user_a")
        self.assertEqual(result["status"], "succeeded", result)
        return result["data"]["risk_profile_id"]

    def start(self, profile, product="WP-C02", amount=50000, account="demo_account_a", key=None):
        request = adapt_request("wealth.purchase",
                                dict(account_id=account, product_id=product,
                                     risk_profile_id=profile, amount_minor=amount),
                                request_id="p_" + uuid4().hex, trace_id="t",
                                idempotency_key=key or "p_" + uuid4().hex)
        return request, self.g.execute(request, principal_id="demo_user_a")

    def reject(self, pending):
        target = pending["next_action"]
        return self.g.authorize(dict(schema_version="1.1", challenge_id=target["challenge_id"],
                                     operation_digest=target["operation_digest"], decision="reject"),
                                principal_id="demo_user_a")

    def verify(self, pending, proof=None):
        target = pending["next_action"]
        token = proof or self.g.issue_demo_proof(target["challenge_id"], principal_id="demo_user_a")
        return self.g.authorize(dict(schema_version="1.1", challenge_id=target["challenge_id"],
                                     operation_digest=target["operation_digest"],
                                     decision="approve", proof_token=token),
                                principal_id="demo_user_a")

    def snapshot(self):
        with self.store.connection() as c:
            account = dict(c.execute("SELECT balance_minor,available_minor,reserved_minor FROM accounts WHERE account_id='demo_account_a'").fetchone())
            holdings = [dict(row) for row in c.execute("SELECT * FROM holdings")]
            orders = [dict(row) for row in c.execute("SELECT * FROM wealth_orders")]
            ledger = [dict(row) for row in c.execute("SELECT * FROM ledger_entries WHERE kind='wealth'")]
            challenges = c.execute("SELECT count(*) FROM challenges").fetchone()[0]
        return account, holdings, orders, ledger, challenges

    def test_d_case_one_purchase(self):
        profile = self.assess((3, 3, 3, 2, 2))  # R3
        request, pending = self.start(profile)
        self.assertEqual(pending["status"], "awaiting_verification")
        verified = self.verify(pending)
        self.assertEqual(verified["status"], "succeeded", verified)
        self.assertEqual(verified["data"]["amount_minor"], 50000)
        validate_result(request, verified)

        account, holdings, orders, ledger, _ = self.snapshot()
        self.assertEqual((account["balance_minor"], account["available_minor"], account["reserved_minor"]),
                         (150000, 150000, 0))
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0]["units"], "499.5000")
        self.assertEqual(holdings[0]["product_id"], "WP-C02")
        self.assertEqual(holdings[0]["lock_days"], 7)
        self.assertEqual(orders[0]["order_type"], "purchase")
        self.assertEqual(orders[0]["fee_minor"], 50)
        self.assertEqual(orders[0]["units"], "499.5")
        self.assertEqual([(row["direction"], row["amount_minor"]) for row in ledger], [("debit", 50000)])

    def test_risk_minimum_and_balance_block_before_confirmation(self):
        r2 = self.assess((2, 2, 1, 2, 2))  # R2
        _, risk = self.start(r2, product="WP-C04", amount=100000)
        self.assertEqual(risk["status"], "failed")
        self.assertEqual(risk["error"]["code"], "RISK_BLOCKED")

        r3 = self.assess((3, 3, 3, 2, 2))  # R3
        _, minimum = self.start(r3, product="WP-C02", amount=9999)
        self.assertEqual(minimum["error"]["code"], "LIMIT_EXCEEDED")

        _, balance = self.start(r3, product="WP-C02", amount=250000)
        self.assertEqual(balance["error"]["code"], "INSUFFICIENT_FUNDS")

        account, holdings, orders, ledger, challenges = self.snapshot()
        self.assertEqual((account["balance_minor"], account["available_minor"]), (200000, 200000))
        self.assertEqual((holdings, orders, ledger, challenges), ([], [], [], 0))

    def test_fee_rounds_half_up_to_cent(self):
        profile = self.assess((3, 3, 3, 2, 2))
        _, pending = self.start(profile, amount=10500)
        result = self.verify(pending)
        self.assertEqual(result["status"], "succeeded")
        _, holdings, orders, _, _ = self.snapshot()
        self.assertEqual(orders[0]["fee_minor"], 11)  # 10.5 分四舍五入
        self.assertEqual(orders[0]["units"], "104.89")
        self.assertEqual(holdings[0]["units"], "104.8900")

    def test_confirmation_reject_leaves_no_side_effect(self):
        profile = self.assess((3, 3, 3, 2, 2))
        _, pending = self.start(profile)
        rejected = self.reject(pending)
        self.assertEqual(rejected["status"], "cancelled")
        account, holdings, orders, ledger, _ = self.snapshot()
        self.assertEqual((account["balance_minor"], holdings, orders, ledger), (200000, [], [], []))

    def test_wrong_proof_keeps_money(self):
        profile = self.assess((3, 3, 3, 2, 2))
        _, pending = self.start(profile)
        with self.assertRaises(Exception) as caught:
            self.verify(pending, proof="wrong")
        self.assertEqual(getattr(caught.exception, "code", None), "AUTH_INVALID")
        account, _, _, _, _ = self.snapshot()
        self.assertEqual(account["balance_minor"], 200000)

    def test_same_key_replays_and_content_conflict(self):
        profile = self.assess((3, 3, 3, 2, 2))
        request, pending = self.start(profile, key="fixed_purchase")
        first = self.verify(pending)
        self.assertEqual(self.g.execute(request, principal_id="demo_user_a"), first)

        other = adapt_request("wealth.purchase",
                              dict(account_id="demo_account_a", product_id="WP-C02",
                                   risk_profile_id=profile, amount_minor=40000),
                              request_id="p2", trace_id="t", idempotency_key="fixed_purchase")
        conflict = self.g.execute(other, principal_id="demo_user_a")
        self.assertEqual(conflict["status"], "failed")
        self.assertEqual(conflict["error"]["code"], "IDEMPOTENCY_CONFLICT")
        account, holdings, _, ledger, _ = self.snapshot()
        self.assertEqual(account["balance_minor"], 150000)
        self.assertEqual(len(holdings), 1)
        self.assertEqual(len(ledger), 1)


if __name__ == "__main__":
    unittest.main()
