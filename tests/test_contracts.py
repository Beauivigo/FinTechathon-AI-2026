"""团队合并代码前运行：python -m unittest discover -s tests -v。"""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from shared.adapters import adapt_request, adapt_result, result_text, yuan_to_minor
from shared.contracts import ACTIONS, ContractError, validate_plan, validate_request, validate_result
from shared.policy import operation_digest, required_gate


def transfer(**changes):
    params = {"account_id": "account_1", "payee_id": "payee_1", "amount_minor": 100}
    params.update(changes)
    return adapt_request("transfer.create", params, request_id="req_1", trace_id="trace_1",
                         idempotency_key="operation_1")


class ContractTests(unittest.TestCase):
    def test_alias_and_exact_money(self):
        request = adapt_request("transfer.create", {
            "accountId": "account_1", "recipient_id": "payee_1", "amount_yuan": "12.50",
        }, request_id="req_1", trace_id="trace_1", idempotency_key="operation_1")
        self.assertEqual(request["params"]["amount_minor"], 1250)
        self.assertEqual(yuan_to_minor("0.01"), 1)
        for invalid in (0.1, True, "1.001", "NaN", "1e2", "-1", "90071992547410.00"):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError):
                yuan_to_minor(invalid)

    def test_ambiguous_or_conflicting_fields_fail(self):
        for extras in ({"amount": "1.00"}, {"amount_yuan": "1.00"}, {"accountId": "account_1"},
                       {"confirmed": True}, {"risk_level": "green"}, {"payee_name": "小王"}):
            with self.subTest(extras=extras), self.assertRaises(ContractError):
                transfer(**extras)

    def test_money_and_write_key_validation(self):
        for amount in (True, 1.0, "100", 0, -1, 2**53):
            with self.subTest(amount=amount), self.assertRaises(ContractError):
                transfer(amount_minor=amount)
        request = transfer()
        del request["idempotency_key"]
        with self.assertRaises(ContractError):
            validate_request(request)

    def test_threshold_includes_current_transfer(self):
        self.assertEqual(required_gate(transfer(amount_minor=100_000), transfer_used_minor=0)["risk_level"], "yellow")
        self.assertEqual(required_gate(transfer(amount_minor=1), transfer_used_minor=100_000)["risk_level"], "red")
        self.assertEqual(required_gate(transfer(amount_minor=12_050), transfer_used_minor=90_000)["risk_level"], "red")
        with self.assertRaises(ContractError):
            required_gate(transfer())

    def test_operation_binding(self):
        request = transfer()
        digest = operation_digest(request, principal_id="user_1")
        self.assertNotEqual(digest, operation_digest(transfer(amount_minor=200), principal_id="user_1"))
        self.assertNotEqual(digest, operation_digest(request, principal_id="user_2"))
        changed = deepcopy(request)
        changed["request_id"] = "retry_req"
        self.assertEqual(digest, operation_digest(changed, principal_id="user_1"))

    def test_success_needs_matching_receipt(self):
        request = transfer()
        result = adapt_result(request, {"status": "OK", "data": {"transaction_id": "tx_1", "amount_minor": 100}},
                              status_map={"OK": "succeeded"}, source="sandbox")
        self.assertEqual(result_text(request, result), "【模拟】转账已完成。")
        for change in ({"request_id": "wrong"}, {"source": "gateway"},
                       {"data": {}}, {"data": {"transaction_id": "tx_1", "amount_minor": 101}}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_result(request, result | change)

    def test_pending_cannot_carry_success_data(self):
        request = transfer()
        with self.assertRaises(ContractError):
            adapt_result(request, {"status": "WAIT", "data": {"transaction_id": "tx_1"}},
                         status_map={"WAIT": "awaiting_confirmation"}, source="gateway")
        with self.assertRaises(ContractError):
            adapt_result(request, {"status": "unexpected"}, status_map={"OK": "succeeded"}, source="sandbox")

    def test_timeout_is_unknown_not_failed(self):
        request = transfer()
        result = adapt_result(request, {"status": "TIMEOUT", "error": {
            "code": "RESULT_UNKNOWN", "message": "需按原操作编号查询",
        }}, status_map={"TIMEOUT": "unknown"}, source="sandbox")
        self.assertIn("暂勿重新提交", result_text(request, result))
        with self.assertRaises(ContractError):
            validate_result(request, result | {"status": "failed"})

    def test_schedule_never_means_transfer_completed(self):
        request = adapt_request("transfer.schedule", {
            "account_id": "account_1", "payee_id": "payee_1", "amount_minor": 100,
            "execute_at": "2026-10-12T09:00:00+08:00",
        }, request_id="req_1", trace_id="trace_1", idempotency_key="operation_1")
        raw = {"status": "CREATED", "data": {"schedule_id": "schedule_1", "execute_at": request["params"]["execute_at"]}}
        result = adapt_result(request, raw, status_map={"CREATED": "scheduled"}, source="sandbox")
        self.assertIn("尚未执行", result_text(request, result))
        with self.assertRaises(ContractError):
            adapt_result(request, raw, status_map={"CREATED": "succeeded"}, source="sandbox")

    def test_dates_and_unknown_actions(self):
        for start, end in (("2026-13-01", "2026-12-31"), ("2026-10-02", "2026-10-01")):
            with self.assertRaises(ContractError):
                adapt_request("bill.analyze", {"account_id": "a", "start_date": start, "end_date": end},
                              request_id="r", trace_id="t")
        with self.assertRaises(ContractError):
            adapt_request("run_python", {}, request_id="r", trace_id="t")
        with self.assertRaises(ContractError):
            adapt_request("subscription.remind", {"subscription_id": "s", "remind_at": "2026-10-12T09:00:00"},
                          request_id="r", trace_id="t", idempotency_key="k")

    def test_plan_dependencies(self):
        steps = [
            {"step_id": "reserve", "action": "budget.reserve", "depends_on": [], "description": "预留预算"},
            {"step_id": "order", "action": "merchant.order", "depends_on": ["reserve"], "description": "下单"},
        ]
        self.assertEqual(validate_plan(steps), steps)
        for first_deps in (["missing"], ["order"], ["reserve"]):
            bad = deepcopy(steps)
            bad[0]["depends_on"] = first_deps
            with self.subTest(first_deps=first_deps), self.assertRaises(ContractError):
                validate_plan(bad)

    def test_all_actions_have_a_working_contract(self):
        from examples.module_examples import build_examples
        fixtures = json.loads((Path(__file__).resolve().parents[1] / "examples/contracts.json").read_text(encoding="utf-8"))
        self.assertEqual(fixtures, build_examples(), "修改契约后请同步导出样例")
        self.assertEqual({row["request"]["action"] for row in fixtures}, set(ACTIONS))
        self.assertEqual(len({spec["scene"] for spec in ACTIONS.values()} - {"公共查询"}), 6)
        for fixture in fixtures:
            with self.subTest(name=fixture["name"]):
                validate_result(fixture["request"], fixture["result"])
                self.assertEqual(result_text(fixture["request"], fixture["result"]), fixture["display_text"])


if __name__ == "__main__":
    unittest.main()
