"""运行 python -m examples.contract_demo；演示接口，不执行或伪造真实转账。"""

import json

from shared.adapters import adapt_request, adapt_result, result_text
from shared.policy import operation_digest, required_gate


def main():
    request = adapt_request("transfer.create", {
        "accountId": "demo_account_1", "recipient_id": "demo_payee_1", "amount_yuan": "120.50",
    }, request_id="request_demo_1", trace_id="trace_demo_1", idempotency_key="operation_demo_1")
    gate = required_gate(request, transfer_used_minor=90_000)
    digest = operation_digest(request, principal_id="demo_user_1")
    # 固定演示凭据不是有效授权；真实 challenge 由 B 的服务端生成并存储。
    pending = adapt_result(request, {"status": "NEED_AUTH", "next_action": {
        "challenge_id": "demo_challenge_1", "kind": "verify", "operation_digest": digest,
        "expires_at": "2026-10-10T20:00:00+08:00", "methods": ["demo_otp"],
    }},
                           status_map={"NEED_AUTH": "awaiting_verification"}, source="gateway")
    print(json.dumps({"request": request, "gate": gate,
                      "operation_digest": digest,
                      "result": pending, "display_text": result_text(request, pending)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
