"""A 的调用流程。替换 gateway 对象即可对接 B 的同名入口。"""

from copy import deepcopy
from uuid import uuid4

from shared import (SCHEMA_VERSION, adapt_request, result_text, validate_context,
                    validate_result, validate_transition)
from .intent import parse_transfer


class Conversation:
    def __init__(self, gateway, *, principal_id):
        self.gateway, self.principal_id = gateway, principal_id
        self.resources = validate_context(gateway.get_context(principal_id=principal_id))
        self.messages, self.candidates = [], []
        self.draft, self.request, self.result = None, None, None
        self.payee = None
        self.trace_id = None

    @property
    def pending(self):
        return bool(self.result and self.result["status"] in {"awaiting_confirmation", "awaiting_verification"})

    def _say(self, text):
        self.messages.append({"role": "assistant", "content": text})
        return text

    def submit(self, text):
        self.messages.append({"role": "user", "content": text})
        if self.pending:
            return self._say("请先确认或拒绝当前操作；修改金额或收款人需要重新发起。")
        # 新输入不能沿用上一笔的成功状态，避免错误输入旁仍显示“当前操作成功”。
        self.request = self.result = None
        self.payee = None
        try:
            parsed = parse_transfer(text, self.draft)
        except ValueError as exc:
            return self._say(str(exc))
        if self.draft is None or parsed["payee_query"] != self.draft["payee_query"]:
            self.trace_id = "trace_" + uuid4().hex
            self.candidates = []
        self.draft = parsed
        if parsed["amount_yuan"] is None:
            return self._say(f"要给{parsed['payee_query']}转多少元？例如输入200元。")
        query = adapt_request("payee.resolve", {"query": parsed["payee_query"], "match_type": "name"},
                              request_id="req_" + uuid4().hex, trace_id=self.trace_id)
        result = validate_result(query, self.gateway.execute(query, principal_id=self.principal_id))
        if result["status"] != "succeeded":
            return self._say(result_text(query, result))
        self.candidates = deepcopy(result["data"]["candidates"])
        if not self.candidates:
            return self._say("没有找到该收款人，请核对姓名。本演示可使用小王或李明。")
        if len(self.candidates) > 1:
            return self._say("找到多位同名收款人，请根据银行和账户尾号选择。")
        return self.select_payee(self.candidates[0]["payee_id"])

    def select_payee(self, payee_id):
        if self.pending:
            return self._say("当前操作已经等待授权，请先处理该操作。")
        choices = [row for row in self.candidates if row["payee_id"] == payee_id]
        if not self.draft or not self.draft["amount_yuan"] or len(choices) != 1:
            raise ValueError("请选择当前查询返回的收款人。")
        accounts = self.resources["accounts"]
        if len(accounts) != 1:
            raise ValueError("本轮演示需要一个明确的付款账户；多账户接入时须先增加用户选择。")
        self.payee = deepcopy(choices[0])
        self.request = adapt_request("transfer.create", {"account_id": accounts[0]["account_id"],
            "payee_id": payee_id, "amount_yuan": self.draft["amount_yuan"]},
            request_id="req_" + uuid4().hex, trace_id=self.trace_id,
            idempotency_key="operation_" + uuid4().hex)
        self.result = validate_result(self.request, self.gateway.execute(self.request, principal_id=self.principal_id))
        self.candidates, self.draft = [], None
        return self._say(result_text(self.request, self.result))

    def decide(self, approve, *, simulate_verification=False):
        if not self.pending:
            return self.result
        challenge = self.result["next_action"]
        payload = {"schema_version": SCHEMA_VERSION, "challenge_id": challenge["challenge_id"],
                   "operation_digest": challenge["operation_digest"], "decision": "approve" if approve else "reject"}
        if approve and challenge["kind"] == "verify":
            if not simulate_verification or not getattr(self.gateway, "demo_mode", False):
                raise ValueError("此操作需要验证；普通确认不能代替强验证。")
            payload["proof_token"] = self.gateway.issue_demo_proof(challenge["challenge_id"], principal_id=self.principal_id)
        result = self.gateway.authorize(payload, principal_id=self.principal_id)
        validate_transition(self.request, self.result, result)
        self.result = result
        self._say(result_text(self.request, result))
        return result
