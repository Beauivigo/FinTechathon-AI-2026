"""明确标记的测试替身。生成静态示例回执，不扣款、不建立银行账本。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import secrets
from uuid import uuid4

from shared import (SCHEMA_VERSION, ServiceError, adapt_result, operation_digest, required_gate,
                    validate_authorization, validate_context, validate_request)


class DemoGateway:
    demo_mode = True
    principal_id = "demo_user_a"
    payees = [
        {"payee_id": "demo_payee_wang_1", "display_name": "小王", "masked_account": "****1234", "bank_name": "模拟银行甲"},
        {"payee_id": "demo_payee_wang_2", "display_name": "小王", "masked_account": "****5678", "bank_name": "模拟银行乙"},
        {"payee_id": "demo_payee_li", "display_name": "李明", "masked_account": "****9012", "bank_name": "模拟银行甲"},
    ]

    def __init__(self):
        self.operations = {}
        self.challenges = {}
        self.receipt_count = 0

    def _identity(self, principal_id):
        if principal_id != self.principal_id:
            raise ServiceError("FORBIDDEN", "演示身份不匹配。")

    def get_context(self, *, principal_id):
        self._identity(principal_id)
        return validate_context({"schema_version": SCHEMA_VERSION, "source": "sandbox",
            "accounts": [{"account_id": "demo_account_a", "display_name": "模拟日常账户"}],
            "products": [], "holdings": [], "cards": [], "items": [], "addresses": [], "questionnaires": []})

    def execute(self, request, *, principal_id):
        self._identity(principal_id)
        validate_request(request)
        if request["action"] == "payee.resolve":
            candidates = ([deepcopy(row) for row in self.payees if row["display_name"] == request["params"]["query"]]
                          if request["params"]["match_type"] == "name" else [])
            return adapt_result(request, {"status": "succeeded", "data": {"candidates": candidates}}, source="sandbox")
        if request["action"] != "transfer.create":
            raise ServiceError("INVALID_REQUEST", "此测试替身只提供收款人查询和普通转账示例。")
        p = request["params"]
        if p["account_id"] != "demo_account_a" or p["payee_id"] not in {row["payee_id"] for row in self.payees}:
            raise ServiceError("FORBIDDEN", "对象不在演示数据中。")
        key = (principal_id, request["action"], request["idempotency_key"])
        digest = operation_digest(request, principal_id=principal_id)
        if key in self.operations:
            saved = self.operations[key]
            if saved["digest"] != digest:
                raise ServiceError("IDEMPOTENCY_CONFLICT", "同一操作编号的内容发生变化。")
            raw = {k: deepcopy(v) for k, v in saved["result"].items() if k in {"status", "data", "error", "next_action"}}
            return adapt_result(request, raw, source=saved["result"]["source"])
        gate = required_gate(request, transfer_used_minor=0)
        kind = "verify" if gate["required_action"] == "verify" else "confirm"
        challenge_id = "demo_challenge_" + uuid4().hex
        challenge = {"challenge_id": challenge_id, "kind": kind, "operation_digest": digest,
                     "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()}
        if kind == "verify":
            challenge["methods"] = ["demo_otp"]
        result = adapt_result(request, {"status": "awaiting_verification" if kind == "verify" else "awaiting_confirmation",
                                       "next_action": challenge}, source="gateway")
        saved = {"digest": digest, "request": deepcopy(request), "result": deepcopy(result),
                 "principal_id": principal_id, "proof_token": None}
        self.operations[key] = saved
        self.challenges[challenge_id] = saved
        return result

    def issue_demo_proof(self, challenge_id, *, principal_id):
        self._identity(principal_id)
        saved = self.challenges.get(challenge_id)
        if not saved or saved["result"]["status"] != "awaiting_verification":
            raise ServiceError("AUTH_INVALID", "模拟验证任务不存在或已结束。")
        saved["proof_token"] = secrets.token_urlsafe(24)
        return saved["proof_token"]

    def authorize(self, payload, *, principal_id):
        self._identity(principal_id)
        validate_authorization(payload)
        saved = self.challenges.get(payload["challenge_id"])
        if not saved or payload["operation_digest"] != saved["digest"]:
            raise ServiceError("AUTH_INVALID", "确认内容与原请求不一致。")
        previous = saved["result"]
        if previous["status"] not in {"awaiting_confirmation", "awaiting_verification"}:
            return deepcopy(previous)
        challenge = previous["next_action"]
        if payload["decision"] == "reject":
            result = adapt_result(saved["request"], {"status": "cancelled"}, source="gateway")
        elif datetime.fromisoformat(challenge["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
            result = adapt_result(saved["request"], {"status": "failed", "error": {
                "code": "AUTH_EXPIRED", "message": "此演示确认已过期，请重新发起。"}}, source="gateway")
        else:
            if challenge["kind"] == "verify" and (not saved["proof_token"] or
                    not secrets.compare_digest(payload.get("proof_token", ""), saved["proof_token"])):
                raise ServiceError("AUTH_INVALID", "请先完成明确标记的模拟验证。")
            self.receipt_count += 1
            result = adapt_result(saved["request"], {"status": "succeeded", "data": {
                "transaction_id": "demo_receipt_" + uuid4().hex,
                "amount_minor": saved["request"]["params"]["amount_minor"]}}, source="sandbox")
        saved["result"] = deepcopy(result)
        saved["proof_token"] = None
        return result
