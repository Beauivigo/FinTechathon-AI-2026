"""JSON 接口唯一口径。只依赖 Python 3.10+ 标准库。"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import math
import re

SCHEMA_VERSION = "1.1"
MAX_SAFE_INTEGER = 2**53 - 1
MAX_JSON_BYTES = 262_144

# 列表元素和嵌套对象也有固定字段，禁止成员各自发明内部结构。
ENUMS = {
    "risk": {"R1", "R2", "R3", "R4", "R5"},
    "card_state": {"active", "frozen", "lost"},
    "subscription_state": {"candidate", "active", "cancelled", "dismissed"},
    "review_decision": {"accept", "reject"},
    "interval": {"weekly", "monthly", "yearly"},
    "currency": {"CNY"}, "direction": {"credit", "debit"},
    "transaction_kind": {"income", "consumption", "transfer", "refund", "wealth", "subscription"},
    "match_type": {"name", "phone"},
    "challenge_kind": {"confirm", "verify"}, "decision": {"approve", "reject"},
    "verification_method": {"sms_otp", "face", "u_key", "demo_otp"},
}
RECORDS = {
    "share": ({"payee_id": "id", "amount_minor": "money"}, {}),
    "category": ({"category": "string", "amount_minor": "nonnegative"}, {}),
    "anomaly": ({"transaction_id": "id", "reason": "string"}, {}),
    "product": ({"product_id": "id", "name": "string", "risk_level": "enum:risk",
                 "minimum_amount_minor": "nonnegative", "lock_days": "nonnegative",
                 "fee_description": "string", "redemption_rule": "string"}, {}),
    "subscription": ({"subscription_id": "id", "merchant_name": "string", "amount_minor": "money",
                      "interval": "enum:interval", "next_charge_date": "date",
                      "subscription_state": "enum:subscription_state", "evidence": "string"}, {}),
    "payee": ({"payee_id": "id", "display_name": "string", "masked_account": "string"},
              {"bank_name": "string"}),
    "transaction": ({"transaction_id": "id", "account_id": "id", "amount_minor": "money",
                     "direction": "enum:direction", "kind": "enum:transaction_kind",
                     "category": "string", "occurred_at": "datetime"}, {"merchant_name": "string"}),
    "challenge": ({"challenge_id": "id", "kind": "enum:challenge_kind", "operation_digest": "digest",
                   "expires_at": "datetime"}, {"methods": "verification_methods"}),
}


class ContractError(ValueError):
    """请求或模块返回值不符合共同约定。"""


# required / optional 是输入字段，output 是成功时必须提供的结果字段。
# money = 人民币整数分；string = 非空字符串；object/list = JSON 对象/数组。
def action(scene, owner, risk, required, output, optional=None, write=False):
    return dict(scene=scene, owner=owner, risk=risk, required=required,
                optional=optional or {}, output=output, write=write)


ACTIONS = {
    "transfer.create": action("智能转账", "B", "dynamic",
        {"account_id": "string", "payee_id": "string", "amount_minor": "money"},
        {"transaction_id": "string", "amount_minor": "money"},
        {"memo": "string"}, write=True),
    "transfer.schedule": action("智能转账", "B", "dynamic",
        {"account_id": "string", "payee_id": "string", "amount_minor": "money", "execute_at": "datetime"},
        {"schedule_id": "string", "execute_at": "datetime"}, write=True),
    "transfer.split": action("智能转账", "B", "yellow",
        {"account_id": "string", "payee_ids": "ids", "amount_minor": "money"},
        {"collection_id": "string", "shares": "list:share"}, write=True),
    "bill.analyze": action("账单分析", "C", "green",
        {"account_id": "string", "start_date": "date", "end_date": "date"},
        {"total_expense_minor": "nonnegative", "categories": "list:category", "anomalies": "list:anomaly", "report": "string"}),
    "wealth.compare": action("理财操作", "D", "green",
        {"product_ids": "ids"}, {"products": "list:product"}),
    "wealth.assess": action("理财操作", "D", "green",
        {"questionnaire_version": "id", "answers": "answers"},
        {"risk_profile_id": "string", "risk_level": "enum:risk"}, write=True),
    "wealth.purchase": action("理财操作", "D", "red",
        {"account_id": "string", "product_id": "string", "risk_profile_id": "string", "amount_minor": "money"},
        {"order_id": "string", "amount_minor": "money"}, write=True),
    "wealth.redeem": action("理财操作", "D", "red",
        {"account_id": "string", "holding_id": "string", "units": "decimal_string"},
        {"order_id": "string", "units": "decimal_string"}, write=True),
    "card.apply": action("卡片管理", "E", "yellow",
        {"account_id": "string"}, {"application_id": "string"}, write=True),
    "card.set_limit": action("卡片管理", "E", "red",
        {"card_id": "string", "limit_minor": "nonnegative"},
        {"card_id": "string", "limit_minor": "nonnegative"}, write=True),
    "card.freeze": action("卡片管理", "E", "red",
        {"card_id": "string"}, {"card_id": "string", "card_state": "enum:card_state"}, write=True),
    "card.unfreeze": action("卡片管理", "E", "red",
        {"card_id": "string"}, {"card_id": "string", "card_state": "enum:card_state"}, write=True),
    "card.report_loss": action("卡片管理", "E", "red",
        {"card_id": "string"}, {"card_id": "string", "card_state": "enum:card_state"}, write=True),
    "card.release_loss": action("卡片管理", "E", "red",
        {"card_id": "id"}, {"card_id": "id", "card_state": "enum:card_state"}, write=True),
    # 题目把订阅查询也列入黄色，保持题目口径。
    "subscription.list": action("订阅/代扣管理", "E", "yellow",
        {"account_id": "string"}, {"subscriptions": "list:subscription"}),
    "subscription.cancel": action("订阅/代扣管理", "E", "yellow",
        {"subscription_id": "string"}, {"subscription_id": "string", "subscription_state": "enum:subscription_state"}, write=True),
    "subscription.review": action("订阅/代扣管理", "E", "yellow",
        {"subscription_id": "id", "decision": "enum:review_decision"},
        {"subscription_id": "id", "subscription_state": "enum:subscription_state"}, write=True),
    "subscription.remind": action("订阅/代扣管理", "E", "yellow",
        {"subscription_id": "string", "remind_at": "datetime"}, {"reminder_id": "string"}, write=True),
    # plan 仅生成计划；真正资金预留、消费仍逐步经过权限网关。
    "scenario.plan": action("跨场景联动", "A", "green",
        {"goal": "string"}, {"steps": "list", "summary": "string"}),
    "budget.reserve": action("跨场景联动", "B", "yellow",
        {"account_id": "string", "amount_minor": "money", "purpose": "string"},
        {"reservation_id": "string", "amount_minor": "money"}, write=True),
    "budget.release": action("跨场景联动", "B", "yellow",
        {"reservation_id": "string"}, {"reservation_id": "string"}, write=True),
    "merchant.order": action("跨场景联动", "B", "red",
        {"account_id": "string", "reservation_id": "string", "item_id": "string", "amount_minor": "money",
         "deliver_on": "date", "delivery_address_id": "id"},
        {"order_id": "string", "amount_minor": "money"}, write=True),
    "account.balance": action("公共查询", "B", "green", {"account_id": "id"},
        {"account_id": "id", "balance_minor": "nonnegative", "available_minor": "nonnegative",
         "reserved_minor": "nonnegative", "currency": "enum:currency"}),
    "payee.resolve": action("公共查询", "B", "green", {"query": "string", "match_type": "enum:match_type"},
        {"candidates": "list:payee"}),
    "transaction.list": action("公共查询", "B", "green",
        {"account_id": "id", "start_date": "date", "end_date": "date"},
        {"transactions": "list:transaction", "next_cursor": "cursor"}, {"cursor": "id", "limit": "page_size"}),
    "operation.get": action("公共查询", "B", "green",
        {"original_action": "string", "original_idempotency_key": "id"},
        {"operation_request": "object", "operation_result": "object"}),
}

# ID 使用同一严格类型，避免空白 ID 或数字 ID 在不同模块里得到不同解释。
for _spec in ACTIONS.values():
    for _group in ("required", "optional", "output"):
        for _name, _kind in _spec[_group].items():
            if _name.endswith("_id") and _kind == "string":
                _spec[_group][_name] = "id"

STATUSES = {
    "succeeded": "已完成", "awaiting_confirmation": "待用户确认",
    "awaiting_verification": "待强验证", "scheduled": "已安排，尚未执行",
    "running": "执行中", "failed": "执行失败", "unknown": "结果待核实",
    "cancelled": "已取消", "blocked": "已阻止",
}
ERROR_CODES = {
    "INVALID_REQUEST", "NOT_FOUND", "FORBIDDEN", "INSUFFICIENT_FUNDS",
    "RISK_BLOCKED", "LIMIT_EXCEEDED", "IDEMPOTENCY_CONFLICT",
    "UPSTREAM_ERROR", "RESULT_UNKNOWN", "SAFETY_LOCKED",
    "AUTH_EXPIRED", "AUTH_INVALID", "INVALID_STATE",
}


def ensure(condition, message):
    if not condition:
        raise ContractError(message)


def json_value(value):
    """统一拒绝非法 JSON、循环容器、过深结构；不泄露原始输入内容。"""
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        ensure(depth <= 32 and count <= 10_000, "JSON 结构过深或过大")
        if type(item) is dict:
            ensure(all(type(k) is str for k in item), "JSON 键必须是字符串")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        else:
            ensure(type(item) in (str, int, float, bool, type(None)), "必须使用 JSON 类型")
            if type(item) is float:
                ensure(math.isfinite(item), "JSON 不接受 NaN/Infinity")
            if type(item) is int:
                ensure(abs(item) <= MAX_SAFE_INTEGER, "整数超出跨语言安全范围")
    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
        ensure(len(encoded) <= MAX_JSON_BYTES, "JSON 超过 256 KiB")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ContractError("不是有效 JSON 数据") from exc


def check_value(value, kind, field):
    valid = False
    if kind == "string":
        valid = type(value) is str and bool(value.strip()) and len(value) <= 4096
    elif kind == "id":
        valid = type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}", value)
    elif kind == "digest":
        valid = type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
    elif kind in ("money", "nonnegative"):
        valid = type(value) is int and (0 < value if kind == "money" else 0 <= value) and value <= MAX_SAFE_INTEGER
    elif kind in ("object", "list"):
        valid = type(value) is (dict if kind == "object" else list)
    elif kind == "ids":
        ensure(type(value) is list and 1 <= len(value) <= 100, f"{field} 必须有 1—100 个 ID")
        for item in value:
            check_value(item, "id", field)
        valid = len(set(value)) == len(value)
    elif kind == "decimal_string":
        valid = (type(value) is str and re.fullmatch(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,8})?", value)
                 and Decimal(value) > 0)
    elif kind == "page_size":
        valid = type(value) is int and 1 <= value <= 100
    elif kind == "cursor":
        if value is not None:
            check_value(value, "id", field)
        valid = True
    elif kind == "answers":
        ensure(type(value) is dict and 1 <= len(value) <= 50, "answers 必须有 1—50 个问答")
        for question, choice in value.items():
            check_value(question, "id", "question_id")
            check_value(choice, "id", "choice_id")
        valid = True
    elif kind.startswith("enum:"):
        valid = type(value) is str and value in ENUMS[kind[5:]]
    elif kind.startswith("list:"):
        ensure(type(value) is list and len(value) <= 100, f"{field} 最多 100 项")
        required, optional = RECORDS[kind[5:]]
        for item in value:
            fields(item, required, optional)
        valid = True
    elif kind == "verification_methods":
        ensure(type(value) is list and 1 <= len(value) <= 4, "验证方法必须为非空列表")
        for item in value:
            check_value(item, "enum:verification_method", field)
        valid = len(set(value)) == len(value)
    elif kind in ("date", "datetime"):
        try:
            ensure(type(value) is str, f"{field} 必须是字符串")
            pattern = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
            if kind == "datetime":
                pattern += r"T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
            ensure(re.fullmatch(pattern, value) is not None, f"{field} 时间格式不规范")
            parsed = date.fromisoformat(value) if kind == "date" else datetime.fromisoformat(value.replace("Z", "+00:00"))
            valid = (parsed.isoformat() == value if kind == "date" else parsed.utcoffset() is not None)
            if kind == "datetime" and valid:
                parsed.astimezone(timezone.utc)
                parsed.astimezone(timezone(timedelta(hours=8)))
        except (ValueError, TypeError, OverflowError):
            valid = False
    ensure(bool(valid), f"{field} 类型/值错误，应为 {kind}")


def fields(value, required, optional=None):
    ensure(type(value) is dict, "字段集合必须是对象")
    json_value(value)
    optional = optional or {}
    ensure(not (required.keys() - value.keys()), f"缺少字段: {sorted(required.keys() - value.keys())}")
    ensure(not (value.keys() - required.keys() - optional.keys()),
           f"未知字段: {sorted(value.keys() - required.keys() - optional.keys())}")
    for name, item in value.items():
        check_value(item, (required | optional)[name], name)


def validate_request(request):
    fields(request, {"schema_version": "string", "request_id": "id", "trace_id": "id",
                     "action": "string", "params": "object"}, {"idempotency_key": "id"})
    ensure(request["schema_version"] == SCHEMA_VERSION, "不支持的 schema_version")
    ensure(request["action"] in ACTIONS, "未知 action，禁止直接执行模型生成的工具名")
    spec = ACTIONS[request["action"]]
    fields(request["params"], spec["required"], spec["optional"])
    if spec["write"]:
        ensure("idempotency_key" in request, "写操作必须有 idempotency_key")
    p = request["params"]
    if "start_date" in p:
        ensure(p["start_date"] <= p["end_date"], "start_date 不能晚于 end_date")
    if request["action"] == "transfer.split":
        ensure(p["amount_minor"] >= len(p["payee_ids"]), "AA 每人至少分配 1 分")
    if request["action"] == "operation.get":
        ensure(p["original_action"] in ACTIONS and ACTIONS[p["original_action"]]["write"],
               "operation.get 只能查询原写操作")
    return request


def validate_result(request, result):
    validate_request(request)
    fields(result, {"schema_version": "string", "request_id": "id", "trace_id": "id",
                   "action": "string", "status": "string", "source": "string", "data": "object"},
           {"error": "object", "next_action": "object"})
    for name in ("schema_version", "request_id", "trace_id", "action"):
        ensure(result[name] == request[name], f"返回值 {name} 与请求不匹配")
    status, data = result["status"], result["data"]
    ensure(status in STATUSES, "未知 status")
    ensure(result["source"] in ("sandbox", "bank", "gateway"), "未知 source")
    if status in ("failed", "unknown", "blocked"):
        fields(result.get("error"), {"code": "string", "message": "string"})
        ensure(result["error"]["code"] in ERROR_CODES, "未知 error.code")
        ensure((status == "unknown") == (result["error"]["code"] == "RESULT_UNKNOWN"),
               "结果未知必须使用 unknown / RESULT_UNKNOWN")
    else:
        ensure("error" not in result, "非错误状态不能带 error")
    if status in ("awaiting_confirmation", "awaiting_verification"):
        ensure(result["source"] == "gateway", "确认/验证只能由网关发起")
        challenge = result.get("next_action")
        fields(challenge, *RECORDS["challenge"])
        kind = "confirm" if status == "awaiting_confirmation" else "verify"
        ensure(challenge["kind"] == kind, "next_action.kind 与状态不匹配")
        ensure(("methods" in challenge) == (kind == "verify"), "强验证必须指定 methods；确认不传 methods")
    else:
        ensure("next_action" not in result, "只有待确认/验证状态可以带 next_action")
    if status == "succeeded":
        spec = ACTIONS[request["action"]]
        ensure(result["source"] != "gateway", "网关不能自行声明业务成功")
        ensure(request["action"] != "transfer.schedule", "创建定时转账只能返回 scheduled")
        fields(data, spec["output"])
        for field in ("amount_minor", "units", "limit_minor", "card_id", "subscription_id", "reservation_id"):
            if field in data and field in request["params"]:
                left, right = data[field], request["params"][field]
                ensure((Decimal(left) == Decimal(right)) if field == "units" else left == right,
                       f"返回值 {field} 与请求不符")
        validate_business_result(request, data)
        if request["action"] == "operation.get":
            ensure(data["operation_result"]["source"] in (result["source"], "gateway"),
                   "原操作来源与查询来源不一致")
        if request["action"] == "scenario.plan":
            validate_plan(data["steps"])
    elif status == "scheduled":
        ensure(request["action"] == "transfer.schedule" and result["source"] != "gateway",
               "scheduled 只用于后端已登记的定时转账")
        fields(data, ACTIONS["transfer.schedule"]["output"])
        ensure(datetime.fromisoformat(data["execute_at"].replace("Z", "+00:00")) ==
               datetime.fromisoformat(request["params"]["execute_at"].replace("Z", "+00:00")), "执行时间不匹配")
    else:
        ensure(data == {}, "未完成状态禁止携带成功数据")
    return result


def unique(records, key):
    ensure(len({row[key] for row in records}) == len(records), f"结果包含重复 {key}")


def validate_business_result(request, data):
    """仅验证可由接口本身判定的一致性；真实账本、权限与风险由业务后端检查。"""
    name, p = request["action"], request["params"]
    if name == "transfer.split":
        unique(data["shares"], "payee_id")
        ensure({row["payee_id"] for row in data["shares"]} == set(p["payee_ids"]), "AA 收款人不匹配")
        amounts = [row["amount_minor"] for row in data["shares"]]
        ensure(sum(amounts) == p["amount_minor"] and max(amounts) - min(amounts) <= 1, "AA 总额或均分结果不符")
    elif name == "bill.analyze":
        unique(data["categories"], "category")
        unique(data["anomalies"], "transaction_id")
        ensure(sum(row["amount_minor"] for row in data["categories"]) == data["total_expense_minor"], "分类金额与总支出不符")
    elif name == "wealth.compare":
        unique(data["products"], "product_id")
        ensure({row["product_id"] for row in data["products"]} == set(p["product_ids"]), "产品列表与请求不符")
    elif name in ("card.freeze", "card.unfreeze", "card.report_loss", "card.release_loss"):
        target = {"card.freeze": "frozen", "card.unfreeze": "active", "card.report_loss": "lost",
                  "card.release_loss": "frozen"}[name]
        ensure(data["card_state"] == target, "卡片结果状态与动作不符")
    elif name == "subscription.cancel":
        ensure(data["subscription_state"] == "cancelled", "取消订阅结果必须为 cancelled")
    elif name == "subscription.review":
        target = "active" if p["decision"] == "accept" else "dismissed"
        ensure(data["subscription_state"] == target, "候选核实结果与 decision 不符")
    elif name == "subscription.list":
        unique(data["subscriptions"], "subscription_id")
    elif name == "payee.resolve":
        unique(data["candidates"], "payee_id")
    elif name == "account.balance":
        ensure(data["account_id"] == p["account_id"], "账户不匹配")
        ensure(data["balance_minor"] == data["available_minor"] + data["reserved_minor"], "余额与可用/预留不符")
    elif name == "transaction.list":
        unique(data["transactions"], "transaction_id")
        ensure(len(data["transactions"]) <= p.get("limit", 100), "超过请求分页数量")
        for row in data["transactions"]:
            ensure(row["account_id"] == p["account_id"], "流水账户不匹配")
            day = datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=8))).date().isoformat()
            ensure(p["start_date"] <= day <= p["end_date"], "流水超出查询日期")
    elif name == "operation.get":
        original = validate_request(data["operation_request"])
        ensure(original["action"] == p["original_action"] and
               original.get("idempotency_key") == p["original_idempotency_key"], "原操作与查询不符")
        validate_result(original, data["operation_result"])


def validate_authorization(payload):
    """确认提交的数据格式；凭证有效性、归属、过期和防重放仍由 B 检查。"""
    fields(payload, {"schema_version": "string", "challenge_id": "id", "operation_digest": "digest",
                     "decision": "enum:decision"}, {"proof_token": "string"})
    ensure(payload["schema_version"] == SCHEMA_VERSION, "不支持的 schema_version")
    ensure(payload["decision"] != "reject" or "proof_token" not in payload, "拒绝操作时不要提交验证凭证")
    return payload


def validate_transition(request, previous, current):
    """校验同一请求的状态更新；持久化、并发和执行授权由网关负责。"""
    validate_result(request, previous)
    validate_result(request, current)
    if previous["source"] != "gateway":
        ensure(current["source"] == previous["source"], "已确定的业务来源不能切换或退回网关")
    if previous == current:
        return current
    transitions = {
        "awaiting_confirmation": {"awaiting_confirmation", "awaiting_verification", "running", "succeeded", "scheduled", "failed", "blocked", "cancelled", "unknown"},
        "awaiting_verification": {"awaiting_verification", "running", "succeeded", "scheduled", "failed", "blocked", "cancelled", "unknown"},
        "running": {"succeeded", "scheduled", "failed", "unknown"},
        "unknown": {"succeeded", "scheduled", "failed"},
    }
    ensure(current["status"] in transitions.get(previous["status"], set()), "非法状态变化或终态结果被改写")
    return current


def validate_plan(steps):
    """DAG = 带先后依赖的步骤表。这里只验证，不执行、不自动授权。"""
    json_value(steps)
    ensure(type(steps) is list and 1 <= len(steps) <= 100, "计划必须有 1—100 个步骤")
    dependencies = {}
    for step in steps:
        fields(step, {"step_id": "id", "action": "string", "depends_on": "list", "description": "string"})
        ensure(step["action"] in ACTIONS and step["action"] != "scenario.plan", "计划中存在非法 action")
        ensure(step["step_id"] not in dependencies, "step_id 重复")
        deps = step["depends_on"]
        for dependency in deps:
            check_value(dependency, "id", "depends_on")
        ensure(len(set(deps)) == len(deps), "depends_on 重复")
        dependencies[step["step_id"]] = set(deps)
    ensure(all(d <= dependencies.keys() for d in dependencies.values()), "依赖步骤不存在")
    remaining = dict(dependencies)
    while remaining:
        ready = {key for key, deps in remaining.items() if not deps}
        ensure(bool(ready), "计划存在循环依赖")
        remaining = {key: deps - ready for key, deps in remaining.items() if key not in ready}
    return steps
