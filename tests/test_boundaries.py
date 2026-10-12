"""针对跨模块误接、嵌套数据、JSON 和授权状态的回归测试。"""

from copy import deepcopy
from decimal import localcontext
import json
import unittest

from examples.module_examples import build_examples
from shared import (SCHEMA_VERSION, ContractError, adapt_request, adapt_result, loads_request, loads_result,
                    operation_digest, required_gate, validate_authorization, validate_result,
                    validate_transition, yuan_to_minor, result_text)
from shared.adapters import strict_json_loads
from shared.contracts import MAX_SAFE_INTEGER, json_value


class BoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.examples = {example["name"]: example for example in build_examples()}

    def case(self, name):
        example = deepcopy(self.examples[name])
        return example["request"], example["result"]

    def test_strict_json_rejects_duplicate_keys_and_constants(self):
        for text in ('{"amount_minor": 1, "amount_minor": 2}', '{"p":{"x":1,"x":2}}',
                     '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{', '"\\ud800"',
                     '[' * 1100 + '0' + ']' * 1100, '{"x":' + '9' * 5000 + '}'):
            with self.subTest(text=text[:50]), self.assertRaises(ContractError):
                strict_json_loads(text)

    def test_json_roundtrip_preserves_correlation(self):
        for example in self.examples.values():
            request = loads_request(json.dumps(example["request"]))
            self.assertEqual(loads_result(request, json.dumps(example["result"])), example["result"])
        request, result = self.case("transfer.create")
        result["request_id"] = "wrong_request"
        with self.assertRaises(ContractError):
            loads_result(request, json.dumps(result))

    def test_invalid_public_inputs_raise_contract_error(self):
        for bad in (None, [], 1, {1: 1, "bad": 2}, {"p": float("nan")}, {"p": object()}):
            with self.subTest(bad=repr(bad)), self.assertRaises(ContractError):
                adapt_request("transfer.create", bad, request_id="r", trace_id="t")
        for bad in (None, {}, [], {"action": []}):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                adapt_result(bad, {"status": "running"}, source="gateway")

    def test_cyclic_and_oversized_inputs_are_rejected(self):
        cycle = {}
        cycle["self"] = cycle
        for bad in (cycle, {"data": "x" * 262145}, {"data": 2**53}, [0] * 10001):
            with self.assertRaises(ContractError):
                json_value(bad)

    def test_money_does_not_depend_on_decimal_context(self):
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(yuan_to_minor("12345.67"), 1234567)
            self.assertEqual(yuan_to_minor("90071992547409.91"), MAX_SAFE_INTEGER)
        for bad in ("90071992547409.92", 10**5000, "1.２", " 1.20 "):
            with self.assertRaises(ContractError):
                yuan_to_minor(bad)

    def test_id_and_timestamp_formats_are_stable(self):
        request, _ = self.case("transfer.schedule")
        for time in ("2026-10-12 09:00:00+08:00", "20261012T090000+0800", "2026-10-12T09:00:00",
                     "2026-10-12T09:00:00+24:00", "0001-01-01T00:00:00+08:00",
                     "9999-12-31T23:59:59Z", "2026-10-12T09:00:00+00:60",
                     "2026-10-12T09:00:00-00:60", "2026-10-12T09:00:00+08:99"):
            with self.subTest(time=time), self.assertRaises(ContractError):
                adapt_request(request["action"], request["params"] | {"execute_at": time},
                              request_id="r", trace_id="t", idempotency_key="k")
        for value in (" account_1", "account_1 ", "", 1, True):
            with self.subTest(value=value), self.assertRaises(ContractError):
                adapt_request("account.balance", {"account_id": value}, request_id="r", trace_id="t")

    def test_hash_canonicalizes_time_and_units(self):
        for action, field, other in (("transfer.schedule", "execute_at", "2026-10-12T01:00:00Z"),
                                     ("wealth.redeem", "units", "12.5")):
            request, _ = self.case(action)
            changed = deepcopy(request)
            changed["params"][field] = other
            self.assertEqual(operation_digest(request, principal_id="u"), operation_digest(changed, principal_id="u"))
        request, result = self.case("transfer.schedule")
        result["data"]["execute_at"] = "2026-10-12T01:00:00Z"
        validate_result(request, result)

    def test_status_map_and_upstream_metadata(self):
        request, _ = self.case("transfer.create")
        for mapping in ({"OK": []}, {"OK": "ready"}, [], {True: "succeeded"}):
            with self.subTest(mapping=mapping), self.assertRaises(ContractError):
                adapt_result(request, {"status": "OK"}, source="sandbox", status_map=mapping)
        with self.assertRaises(ContractError):
            adapt_result(request, {"request_id": "different", "status": "running"}, source="sandbox")

    def test_alias_adapter_does_not_mutate_input(self):
        request, _ = self.case("wealth.assess")
        params = deepcopy(request["params"])
        adapted = adapt_request(request["action"], params, request_id="r", trace_id="t", idempotency_key="k")
        adapted["params"]["answers"]["q1"] = "changed"
        self.assertEqual(params, request["params"])

    def test_pending_requires_valid_challenge(self):
        request, result = self.case("transfer.verify")
        for change in ({"source": "sandbox"}, {"next_action": {}},
                       {"next_action": result["next_action"] | {"kind": "confirm"}},
                       {"next_action": result["next_action"] | {"methods": []}},
                       {"next_action": result["next_action"] | {"operation_digest": "not-a-hash"}},
                       {"data": {"transaction_id": "fake"}}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_result(request, result | change)
        with self.assertRaises(ContractError):
            adapt_result(request, {"status": "awaiting_confirmation"}, source="gateway")

    def test_authorization_is_separate_from_business_params(self):
        _, result = self.case("transfer.verify")
        challenge = result["next_action"]
        payload = {"schema_version": SCHEMA_VERSION, "challenge_id": challenge["challenge_id"],
                   "operation_digest": challenge["operation_digest"], "decision": "approve", "proof_token": "demo_token"}
        validate_authorization(payload)
        for change in ({"confirmed": True}, {"decision": "reject"}, {"schema_version": "99"}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_authorization(payload | change)

    def test_success_cannot_be_rewritten_and_unknown_can_resolve(self):
        request, success = self.case("transfer.create")
        _, unknown = self.case("transfer.unknown")
        validate_transition(request, unknown, success)
        validate_transition(request, success, deepcopy(success))
        for result in (unknown, success | {"data": success["data"] | {"transaction_id": "other"}}):
            with self.assertRaises(ContractError):
                validate_transition(request, success, result)
        _, verify = self.case("transfer.verify")
        _, confirm = self.case("transfer.confirm")
        with self.assertRaises(ContractError):
            validate_transition(request, verify, confirm)

    def test_aa_members_total_and_fair_split(self):
        request, result = self.case("transfer.split")
        for shares in ([], [{"payee_id": "other", "amount_minor": 101}],
                       [{"payee_id": "payee_1", "amount_minor": 51}, {"payee_id": "payee_1", "amount_minor": 50}],
                       [{"payee_id": "payee_1", "amount_minor": 100}, {"payee_id": "payee_2", "amount_minor": 1}],
                       [{"payee_id": "payee_1", "amount_minor": 51}, {"payee_id": "payee_2", "amount_minor": 51}]):
            with self.subTest(shares=shares), self.assertRaises(ContractError):
                validate_result(request, result | {"data": result["data"] | {"shares": shares}})

    def test_bill_total_and_nested_fields(self):
        request, result = self.case("bill.analyze")
        for change in ({"categories": [{"category": "餐饮", "amount_minor": 1}]},
                       {"categories": [{"name": "餐饮", "amount": 1250}]},
                       {"anomalies": [{"transaction_id": "t", "reason": ""}]},
                       {"categories": [{"category": "餐饮", "amount_minor": 625}] * 2}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_result(request, result | {"data": result["data"] | change})
        validate_result(request, result | {"data": {"categories": [], "total_expense_minor": 0, "anomalies": [], "report": "暂无记录"}})

    def test_products_must_match_and_risk_enum_is_fixed(self):
        request, result = self.case("wealth.compare")
        for products in ([], [result["data"]["products"][0] | {"product_id": "other"}],
                         [result["data"]["products"][0] | {"risk_level": "low"}]):
            with self.subTest(products=products), self.assertRaises(ContractError):
                validate_result(request, result | {"data": {"products": products}})
        request, _ = self.case("wealth.assess")
        for answers in ({}, {"q1": True}, {"q1": 1}):
            with self.subTest(answers=answers), self.assertRaises(ContractError):
                adapt_request("wealth.assess", request["params"] | {"answers": answers}, request_id="r", trace_id="t", idempotency_key="k")

    def test_wrong_card_or_subscription_target_is_rejected(self):
        for name, field, wrong in (("card.freeze", "card_state", "active"), ("card.unfreeze", "card_state", "lost"),
                                   ("card.report_loss", "card_state", "frozen"),
                                   ("subscription.cancel", "subscription_state", "active")):
            request, result = self.case(name)
            with self.subTest(name=name), self.assertRaises(ContractError):
                validate_result(request, result | {"data": result["data"] | {field: wrong}})

    def test_balance_must_reconcile(self):
        request, result = self.case("account.balance")
        for change in ({"available_minor": 1}, {"account_id": "other"}, {"currency": "USD"}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_result(request, result | {"data": result["data"] | change})

    def test_ledger_date_account_and_page_size(self):
        request, result = self.case("transaction.list")
        row = result["data"]["transactions"][0]
        for change in ({"account_id": "other"}, {"occurred_at": "2026-11-01T00:00:00+08:00"}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_result(request, result | {"data": {"transactions": [row | change], "next_cursor": None}})
        # UTC 9月30日16:00 已经是上海10月1日，应该落入查询范围。
        validate_result(request, result | {"data": {"transactions": [row | {"occurred_at": "2026-09-30T16:00:00Z"}], "next_cursor": None}})
        with self.assertRaises(ContractError):
            adapt_request("transaction.list", request["params"] | {"limit": 101}, request_id="r", trace_id="t")

    def test_original_operation_lookup_cannot_swap_operation(self):
        request, result = self.case("operation.get")
        result["data"]["operation_request"]["idempotency_key"] = "other"
        with self.assertRaises(ContractError):
            validate_result(request, result)
        with self.assertRaises(ContractError):
            adapt_request("operation.get", {"original_action": "operation.get", "original_idempotency_key": "k"}, request_id="r", trace_id="t")

    def test_lookup_success_still_displays_unknown_original_result(self):
        request, result = self.case("operation.get")
        text = result_text(request, result)
        self.assertIn("结果待核实", text)
        self.assertIn("暂勿重新提交", text)
        self.assertNotIn("已完成", text)
        with self.assertRaises(ContractError):
            validate_result(request, result | {"source": "bank"})

    def test_gate_output_cannot_be_confused_with_business_status(self):
        request, _ = self.case("account.balance")
        gate = required_gate(request)
        self.assertNotIn("status", gate)
        self.assertEqual(gate["required_action"], "execute")
        request, _ = self.case("transfer.create")
        for used in (True, -1, float("inf"), MAX_SAFE_INTEGER + 1):
            with self.subTest(used=used), self.assertRaises(ContractError):
                required_gate(request, transfer_used_minor=used)


if __name__ == "__main__":
    unittest.main()
