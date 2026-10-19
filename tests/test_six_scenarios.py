"""六场景联调：转账、账单、理财、卡片、订阅、生日。"""

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


class SixScenarioTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / "six.sqlite3"); initialize(self.store)
        self.now = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
        self.g = BankGateway(self.store, clock=lambda: self.now)

    # --- helpers ---
    def req(self, action, params, key=None):
        return adapt_request(action, params, request_id="r_" + uuid4().hex, trace_id="t",
                             idempotency_key=key or "k_" + uuid4().hex)

    def finish(self, pending, principal="demo_user_a"):
        if pending["status"] == "awaiting_confirmation":
            n = pending["next_action"]
            pending = self.g.authorize(dict(schema_version="1.1", challenge_id=n["challenge_id"],
                                            operation_digest=n["operation_digest"], decision="approve"),
                                       principal_id=principal)
        if pending["status"] == "awaiting_verification":
            n = pending["next_action"]
            proof = self.g.issue_demo_proof(n["challenge_id"], principal_id=principal)
            pending = self.g.authorize(dict(schema_version="1.1", challenge_id=n["challenge_id"],
                                            operation_digest=n["operation_digest"],
                                            decision="approve", proof_token=proof),
                                       principal_id=principal)
        return pending

    def call(self, action, params, key=None, principal="demo_user_a"):
        request = self.req(action, params, key)
        result = self.finish(self.g.execute(request, principal_id=principal), principal)
        validate_result(request, result)
        return result

    def balance(self):
        with self.store.connection() as c:
            row = c.execute("SELECT balance_minor,available_minor,reserved_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()
        self.assertEqual(row["balance_minor"], row["available_minor"] + row["reserved_minor"])
        return tuple(row)

    def test_six_scenarios(self):
        # 1 转账：单笔（黄）、定时（只登记）、AA（黄）
        self.assertEqual(self.call("transfer.create", dict(account_id="demo_account_a",
                           payee_id="demo_payee_li", amount_minor=20000))["status"], "succeeded")
        self.assertEqual(self.call("transfer.schedule", dict(account_id="demo_account_a",
                           payee_id="demo_payee_li", amount_minor=10000,
                           execute_at="2026-10-20T09:00:00+08:00"))["status"], "scheduled")
        self.assertEqual(self.call("transfer.split", dict(account_id="demo_account_a",
                           payee_ids=["demo_payee_li", "demo_payee_wang_1"], amount_minor=3000))["status"], "succeeded")
        self.assertEqual(self.balance(), (180000, 180000, 0))

        # 6 生日（先产生消费，供账单断言）：预留 35700 → 下单 19900 → 释放 15800
        reserve = self.call("budget.reserve", dict(account_id="demo_account_a", amount_minor=35700,
                                                   purpose="生日预算"))
        self.assertEqual(reserve["status"], "succeeded")
        order = self.call("merchant.order", dict(account_id="demo_account_a",
                          reservation_id=reserve["data"]["reservation_id"], item_id="ITEM-BD-FLOWER-01",
                          amount_minor=19900, deliver_on="2026-11-20", delivery_address_id="ADDR-BD-01"))
        self.assertEqual(order["status"], "succeeded")
        self.call("budget.release", dict(reservation_id=reserve["data"]["reservation_id"], ))
        self.assertEqual(self.balance(), (160100, 160100, 0))

        # 2 账单：毛支出 19900、鲜花分类、无异常
        bill = self.call("bill.analyze", dict(account_id="demo_account_a", start_date="2026-10-01",
                                              end_date="2026-10-31"))
        self.assertEqual(bill["data"]["total_expense_minor"], 19900)
        self.assertIn({"category": "鲜花", "amount_minor": 19900}, bill["data"]["categories"])
        self.assertEqual(bill["data"]["anomalies"], [])

        # 3 理财：评估（绿）→ 申购（红）→ 解锁后赎回（红）
        profile = self.g.execute(self.req("wealth.assess", dict(questionnaire_version="wpq_v1",
                    answers={f"q{i}": f"q{i}_{LETTERS[s]}" for i, s in enumerate((3, 3, 3, 2, 2), 1)})),
                    principal_id="demo_user_a")
        self.assertEqual(profile["status"], "succeeded")
        purchase = self.call("wealth.purchase", dict(account_id="demo_account_a", product_id="WP-C02",
                            risk_profile_id=profile["data"]["risk_profile_id"], amount_minor=50000))
        self.assertEqual(purchase["status"], "succeeded")
        with self.store.connection() as c:
            holding = c.execute("SELECT holding_id, units FROM holdings WHERE product_id='WP-C02'").fetchone()
        self.assertEqual(holding["units"], "499.5000")
        self.now += timedelta(days=7)
        redeem = self.call("wealth.redeem", dict(account_id="demo_account_a", holding_id=holding["holding_id"],
                           units="200.0000"))
        self.assertEqual(redeem["status"], "succeeded")
        self.assertEqual(self.balance(), (130100, 130100, 0))

        # 4 卡片：申请→限额→冻结/解冻→挂失/解挂
        self.assertEqual(self.call("card.apply", dict(account_id="demo_account_li"),
                                   principal="demo_user_li")["status"], "succeeded")
        self.assertEqual(self.call("card.set_limit", dict(card_id="CARD-E001", limit_minor=60000))["status"], "succeeded")
        self.assertEqual(self.call("card.freeze", dict(card_id="CARD-E001"))["data"]["card_state"], "frozen")
        self.assertEqual(self.call("card.unfreeze", dict(card_id="CARD-E001"))["data"]["card_state"], "active")
        self.assertEqual(self.call("card.report_loss", dict(card_id="CARD-E001"))["data"]["card_state"], "lost")
        self.assertEqual(self.call("card.release_loss", dict(card_id="CARD-E001"))["data"]["card_state"], "frozen")
        self.assertEqual(self.call("card.unfreeze", dict(card_id="CARD-E001"))["data"]["card_state"], "active")

        # 5 订阅：核实→（到期）续费→提醒→取消
        self.assertEqual(self.call("subscription.review", dict(subscription_id="SUB-E001", decision="accept"))["data"]["subscription_state"], "active")
        with self.store.connection(write=True) as c:
            c.execute("UPDATE subscriptions SET next_charge_date='2026-10-26' WHERE subscription_id='SUB-E001'")
        charged = self.call("subscription.charge", dict(subscription_id="SUB-E001"))
        self.assertEqual(charged["data"]["charged_minor"], 2500)
        self.assertEqual(self.call("subscription.remind", dict(subscription_id="SUB-E001",
                           remind_at="2026-11-15T09:00:00+08:00"))["status"], "succeeded")
        self.assertEqual(self.call("subscription.cancel", dict(subscription_id="SUB-E001"))["data"]["subscription_state"], "cancelled")
        self.assertEqual(self.balance(), (127600, 127600, 0))

        # 失败分支：余额不足、理财风险不匹配
        insufficient = self.finish(self.g.execute(self.req("transfer.create", dict(
            account_id="demo_account_a", payee_id="demo_payee_li", amount_minor=99999999)),
            principal_id="demo_user_a"))
        self.assertEqual(insufficient["status"], "failed")
        self.assertEqual(insufficient["error"]["code"], "INSUFFICIENT_FUNDS")
        risk = self.call("wealth.purchase", dict(account_id="demo_account_a", product_id="WP-C04",
            risk_profile_id=profile["data"]["risk_profile_id"], amount_minor=100000))
        self.assertEqual(risk["status"], "failed")
        self.assertEqual(risk["error"]["code"], "RISK_BLOCKED")


if __name__ == "__main__":
    unittest.main()
