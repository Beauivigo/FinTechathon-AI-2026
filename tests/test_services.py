"""1.1 共用前回归：后端来源、候选核实、资源关联、资金回执与调度生命周期。"""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from examples.module_examples import build_examples
from examples.service_examples import build_service_examples
from shared import (ContractError, ServiceError, adapt_request, adapt_result, result_text,
                    validate_transition, validate_context, validate_funds_command,
                    validate_funds_receipt, validate_task_spec, validate_task_transition,
                    validate_card_change, validate_subscription_change, validate_authorization,
                    loads_request)


class ServiceContractTests(unittest.TestCase):
    def setUp(self):
        self.public = {row["name"]: row for row in build_examples()}
        self.internal = build_service_examples()

    def test_business_source_cannot_change_or_hop_through_gateway(self):
        request = self.public["transfer.create"]["request"]
        unknown = self.public["transfer.unknown"]["result"]
        success = self.public["transfer.create"]["result"]
        for original_source, target_source in (("sandbox", "bank"), ("bank", "sandbox")):
            with self.subTest(source=original_source), self.assertRaises(ContractError):
                validate_transition(request, unknown | {"source": original_source}, success | {"source": target_source})
        running = adapt_result(request, {"status": "running"}, source="sandbox")
        with self.assertRaises(ContractError):
            validate_transition(request, running, unknown | {"source": "gateway"})
        validate_transition(request, self.public["transfer.verify"]["result"], success)
        validate_transition(request, unknown, success)
        self.assertIn("【模拟】", result_text(request, success))

    def test_subscription_review_is_not_payment_authorization_or_cancellation(self):
        for decision, state, wording in (("accept", "active", "未新签代扣授权"), ("reject", "dismissed", "未取消任何代扣")):
            request = adapt_request("subscription.review", {"subscription_id": "s", "decision": decision},
                                    request_id="r", trace_id="t", idempotency_key="k")
            result = adapt_result(request, {"status": "succeeded", "data": {
                "subscription_id": "s", "subscription_state": state}}, source="sandbox")
            self.assertIn(wording, result_text(request, result))
            validate_subscription_change(request, "candidate", state)
            with self.assertRaises(ContractError):
                validate_subscription_change(request, "active", state)
            with self.assertRaises(ContractError):
                adapt_result(request, {"status": "succeeded", "data": {
                    "subscription_id": "s", "subscription_state": "cancelled"}}, source="sandbox")
        cancel = self.public["subscription.cancel"]["request"]
        validate_subscription_change(cancel, "active", "cancelled")
        for previous in ("candidate", "dismissed", "cancelled"):
            with self.subTest(previous=previous), self.assertRaises(ContractError):
                validate_subscription_change(cancel, previous, "cancelled")

    def test_subscription_evidence_is_required_and_cannot_be_empty(self):
        sample = self.public["subscription.list"]
        raw = {"status": "succeeded", "data": deepcopy(sample["result"]["data"])}
        row = raw["data"]["subscriptions"][0]
        for bad in (None, "", "  "):
            row["evidence"] = bad
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                adapt_result(sample["request"], raw, source="sandbox")
        del row["evidence"]
        with self.assertRaises(ContractError):
            adapt_result(sample["request"], raw, source="sandbox")

    def test_lost_card_requires_release_then_unfreeze(self):
        validate_card_change("card.release_loss", "lost", "frozen")
        validate_card_change("card.unfreeze", "frozen", "active")
        for action, previous, current in (("card.unfreeze", "lost", "active"),
                                          ("card.release_loss", "lost", "active"),
                                          ("card.freeze", "lost", "frozen")):
            with self.subTest(action=action), self.assertRaises(ContractError):
                validate_card_change(action, previous, current)
        sample = self.public["card.release_loss"]
        with self.assertRaises(ContractError):
            adapt_result(sample["request"], {"status": "succeeded", "data": {
                "card_id": "card_1", "card_state": "active"}}, source="sandbox")

    def test_resource_snapshot_rejects_dangling_or_duplicate_ids(self):
        for group, key, value in (("cards", "account_id", "other"), ("holdings", "product_id", "other")):
            context = deepcopy(self.internal["context"])
            context[group][0][key] = value
            with self.subTest(group=group), self.assertRaises(ContractError):
                validate_context(context)
        context = deepcopy(self.internal["context"])
        context["accounts"] *= 2
        with self.assertRaises(ContractError):
            validate_context(context)
        context = deepcopy(self.internal["context"])
        context["addresses"][0]["full_address"] = "not an agreed field"
        with self.assertRaises(ContractError):
            validate_context(context)

    def test_resource_snapshot_accepts_empty_lists_but_not_invalid_questionnaires(self):
        context = self.internal["context"]
        empty = {key: ([] if isinstance(value, list) else value) for key, value in context.items()}
        validate_context(empty)
        for choices in ([], [{"choice_id": "c", "text": "A"}] * 2):
            bad = deepcopy(context)
            bad["questionnaires"][0]["questions"][0]["choices"] = choices
            with self.subTest(choices=choices), self.assertRaises(ContractError):
                validate_context(bad)

    def test_funds_receipt_cannot_swap_amount_account_or_operation(self):
        pair = self.internal["funds"][0]
        for key, value in (("amount_minor", 101), ("account_id", "other"), ("posting_key", "other"),
                           ("reference_id", "other"), ("effect", "credit")):
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_funds_receipt(pair["command"], pair["receipt"] | {key: value})

    def test_funds_reservation_and_precision_boundaries(self):
        command = self.internal["funds"][0]["command"]
        for change in ({"amount_minor": 1.2}, {"amount_minor": True}, {"amount_minor": 0},
                       {"effect": "consume_reserved"}, {"reservation_id": "r"}, {"effect": "execute_sql"}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_funds_command(command | change)

    def test_tasks_cannot_run_unapproved_action_or_reuse_registration_key(self):
        spec = self.internal["tasks"][0]["spec"]
        for change in ({"kind": "run_python"}, {"trace_id": "wrong"}, {"origin_action": "merchant.order"},
                       {"origin_idempotency_key": spec["payload"]["idempotency_key"]},
                       {"run_at": "2026-10-12T09:00:00"}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_task_spec(spec | change)
        wrong = deepcopy(spec)
        wrong["payload"] = self.public["card.report_loss"]["request"]
        with self.assertRaises(ContractError):
            validate_task_spec(wrong)

    def test_task_claim_cancel_and_unknown_are_distinct(self):
        scheduled = self.internal["tasks"][0]
        running = scheduled | {"state": "running"}
        unknown = scheduled | {"state": "unknown"}
        dispatched = scheduled | {"state": "dispatched"}
        cancelled = scheduled | {"state": "cancelled"}
        validate_task_transition(scheduled, running)
        validate_task_transition(scheduled, cancelled)
        validate_task_transition(running, unknown)
        validate_task_transition(unknown, dispatched)
        for previous, current in ((running, cancelled), (unknown, cancelled), (cancelled, running), (dispatched, running)):
            with self.subTest(previous=previous["state"]), self.assertRaises(ContractError):
                validate_task_transition(previous, current)
        with self.assertRaises(ContractError):
            validate_task_transition(scheduled, running | {"spec": scheduled["spec"] | {"task_key": "new"}})

    def test_internal_calls_are_not_model_actions(self):
        for action in ("post_funds", "register_task", "get_context", "cancel_task"):
            with self.subTest(action=action), self.assertRaises(ContractError):
                adapt_request(action, {}, request_id="r", trace_id="t", idempotency_key="k")

    def test_shared_version_rejects_old_requests_and_authorization(self):
        request = self.public["transfer.create"]["request"] | {"schema_version": "1.0"}
        with self.assertRaises(ContractError):
            loads_request(json.dumps(request))
        challenge = self.public["transfer.confirm"]["result"]["next_action"]
        with self.assertRaises(ContractError):
            validate_authorization({"schema_version": "1.0", "challenge_id": challenge["challenge_id"],
                                    "operation_digest": challenge["operation_digest"], "decision": "approve"})

    def test_service_errors_have_known_codes(self):
        self.assertEqual(ServiceError("RESULT_UNKNOWN", "需查询原操作").code, "RESULT_UNKNOWN")
        with self.assertRaises(ContractError):
            ServiceError("OK", "not an error")

    def test_internal_fixture_matches_generator(self):
        saved = json.loads((Path(__file__).resolve().parents[1] / "examples/internal_contracts.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, self.internal)


if __name__ == "__main__":
    unittest.main()
