"""P2 单笔转账模块。只能在可信外层事务/上下文中调用；不执行授权、不自行提交。"""

from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
import json

from shared import ServiceError, adapt_result, validate_transition
from shared.contracts import MAX_SAFE_INTEGER, ContractError, validate_request
from shared.services import validate_funds_receipt
from .services import SandboxServices
from .store import utc_timestamp


def split_shares(request):
    """B V1.1：按输入顺序给前 r 人多分 1 分；只计算，不付款。"""
    validate_request(request)
    if request['action'] != 'transfer.split':
        raise ServiceError('INVALID_REQUEST', '需要 AA 收款单请求。')
    p = request['params']
    quotient, remainder = divmod(p['amount_minor'], len(p['payee_ids']))
    return [dict(payee_id=payee, amount_minor=quotient + (i < remainder))
            for i, payee in enumerate(p['payee_ids'])]


def handle(params, *, context):
    if (type(context) is not dict or set(context) != {"principal_id", "request", "services", "transaction"}
            or type(context["services"]) is not SandboxServices):
        raise ServiceError("FORBIDDEN", "只接受可信服务端转账上下文。")
    services = context["services"]
    state = services._transfer_state(context["transaction"], context["principal_id"])
    request = state["request"]
    try:
        validate_request(context["request"])
        validate_request(request | {"params": params})
    except ContractError as exc:
        raise ServiceError("INVALID_REQUEST", "转账参数不符合接口约定。") from exc
    if context["request"] != request or params != request["params"]:
        raise ServiceError("FORBIDDEN", "转账内容与已绑定请求不一致。")
    params = request["params"]  # 后续只用事务私有快照，不读取调用者可变的参数对象。
    if state["replay"] is not None:
        state["completed"] = True
        return deepcopy(state["replay"])
    connection = state["connection"]
    amount = params["amount_minor"]
    # 这里只检查整数累计上限；1000 元分级与授权在 P3 网关执行。
    local = datetime.fromisoformat(state["occurred_at"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=8)))
    lower = utc_timestamp(datetime.combine(local.date(), time.min, local.tzinfo).isoformat())
    upper = utc_timestamp(datetime.combine(local.date(), time.max, local.tzinfo).isoformat())
    used = sum(row[0] for row in connection.execute(
        "SELECT amount_minor FROM transfers WHERE principal_id=? AND occurred_at BETWEEN ? AND ?",
        (context["principal_id"], lower, upper)))
    if used + amount > MAX_SAFE_INTEGER:
        raise ServiceError("LIMIT_EXCEEDED", "日累计超出整数金额范围。")
    connection.execute("INSERT INTO transfers VALUES (?,?,?,?,?,?)",
        (state["reference"], context["principal_id"], params["account_id"], params["payee_id"], amount, state["occurred_at"]))
    for command in state["commands"].values():
        command = deepcopy(command)
        receipt = services.post_funds(context["transaction"], command, principal_id=context["principal_id"])
        validate_funds_receipt(command, receipt)
    raw = {"status": "succeeded", "data": {"transaction_id": state["reference"], "amount_minor": amount}}
    result = adapt_result(request, raw, source="sandbox")
    if state["previous"]:
        validate_transition(request, state["previous"], result)
    connection.execute("""INSERT INTO operations VALUES (?,?,?,?,?,?)
        ON CONFLICT(principal_id,action,idempotency_key) DO UPDATE SET result_json=excluded.result_json""",
        (context["principal_id"], request["action"], request["idempotency_key"], state["digest"],
         json.dumps(request), json.dumps(result)))
    connection.execute("""INSERT INTO audit_events
        (principal_id,trace_id,action,outcome,occurred_at,details_json) VALUES (?,?,?,?,?,?)""",
        (context["principal_id"], request["trace_id"], request["action"], "succeeded", state["occurred_at"],
         json.dumps({"idempotency_key": request["idempotency_key"], "transaction_id": state["reference"], "amount_minor": amount})))
    state["replay"] = deepcopy(raw)
    state["completed"] = True
    return raw  # 调用方必须等外层事务成功提交后，才能向用户报告成功。
