"""基于题目规则给出权限要求，不验证身份，也不放行交易。"""

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

from .contracts import ACTIONS, MAX_SAFE_INTEGER, check_value, ensure, validate_request


def operation_digest(request, *, principal_id):
    """绑定具体用户和操作；这是摘要，不是签名/确认凭证。"""
    validate_request(request)
    check_value(principal_id, "id", "principal_id")
    params = deepcopy(request["params"])
    spec = ACTIONS[request["action"]]
    for name, kind in (spec["required"] | spec["optional"]).items():
        if name not in params:
            continue
        if kind == "datetime":
            params[name] = datetime.fromisoformat(params[name].replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        elif kind == "decimal_string" and "." in params[name]:
            params[name] = params[name].rstrip("0").rstrip(".")
    payload = {"schema_version": request["schema_version"], "principal_id": principal_id,
               "action": request["action"], "params": params,
               "idempotency_key": request.get("idempotency_key")}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def required_gate(request, *, transfer_used_minor=None):
    """transfer_used_minor 必须由服务端账本读取，含当天已付及并发预占金额。"""
    validate_request(request)
    risk = ACTIONS[request["action"]]["risk"]
    reason = "题目规定或团队采用的保守权限分级"
    if risk == "dynamic":
        ensure(type(transfer_used_minor) is int and 0 <= transfer_used_minor <= MAX_SAFE_INTEGER,
               "转账权限判断必须提供可信的当日累计金额（整数分）")
        projected = transfer_used_minor + request["params"]["amount_minor"]
        risk = "yellow" if projected <= 100_000 else "red"
        reason = "含本次的当日累计转账≤1000元" if risk == "yellow" else "含本次的当日累计转账>1000元"
    return {
        "risk_level": risk,
        "required_action": {"green": "execute", "yellow": "confirm", "red": "verify"}[risk],
        "reason": reason,
    }
