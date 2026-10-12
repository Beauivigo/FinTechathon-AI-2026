"""服务端内部交接契约；不注册为模型工具，不实现账本、授权或调度器。"""

from typing import Protocol

from .contracts import (ACTIONS, ERROR_CODES, SCHEMA_VERSION, check_value,
                        ensure, fields, unique, validate_request)


class ServiceError(Exception):
    """后端按错误码交给网关处理；写入结果不确定必须使用 RESULT_UNKNOWN。"""

    def __init__(self, code, message):
        check_value(code, "string", "code")
        ensure(code in ERROR_CODES, "未知内部服务错误码")
        check_value(message, "string", "message")
        self.code, self.message = code, message
        super().__init__(message)


def records(rows, required, key):
    ensure(type(rows) is list and len(rows) <= 100, "内部资源列表最多100项")
    for row in rows:
        fields(row, required)
    unique(rows, key)


def validate_context(context):
    """校验登录后由后端提供的资源快照；实际归属与实时状态仍由后端检查。"""
    fields(context, {"schema_version": "string", "source": "string", "accounts": "list",
                     "products": "list:product", "holdings": "list", "cards": "list",
                     "items": "list", "addresses": "list", "questionnaires": "list"})
    ensure(context["schema_version"] == SCHEMA_VERSION, "不支持的 schema_version")
    ensure(context["source"] in ("sandbox", "bank"), "资源来源必须是业务后端")
    records(context["accounts"], {"account_id": "id", "display_name": "string"}, "account_id")
    unique(context["products"], "product_id")
    records(context["holdings"], {"holding_id": "id", "account_id": "id", "product_id": "id",
                                  "units": "decimal_string"}, "holding_id")
    records(context["cards"], {"card_id": "id", "account_id": "id", "card_state": "enum:card_state",
                               "limit_minor": "nonnegative"}, "card_id")
    records(context["items"], {"item_id": "id", "name": "string", "price_minor": "money"}, "item_id")
    records(context["addresses"], {"delivery_address_id": "id", "label": "string"}, "delivery_address_id")
    records(context["questionnaires"], {"questionnaire_version": "id", "questions": "list"}, "questionnaire_version")
    accounts = {row["account_id"] for row in context["accounts"]}
    products = {row["product_id"] for row in context["products"]}
    for row in context["cards"] + context["holdings"]:
        ensure(row["account_id"] in accounts, "资源账户不在当前快照中")
    for row in context["holdings"]:
        ensure(row["product_id"] in products, "持仓产品不在当前快照中")
    for questionnaire in context["questionnaires"]:
        questions = questionnaire["questions"]
        ensure(1 <= len(questions) <= 50, "问卷必须有1—50题")
        records(questions, {"question_id": "id", "text": "string", "choices": "list"}, "question_id")
        for question in questions:
            ensure(2 <= len(question["choices"]) <= 20, "每题必须有2—20个选项")
            records(question["choices"], {"choice_id": "id", "text": "string"}, "choice_id")
    return context


def validate_funds_command(command):
    """一个记账子动作的格式。posting_key 区分同笔业务的不同子记账。"""
    fields(command, {"schema_version": "string", "posting_key": "id", "trace_id": "id",
                     "account_id": "id", "reference_id": "id", "effect": "string",
                     "amount_minor": "money", "kind": "enum:transaction_kind"},
           {"reservation_id": "id"})
    ensure(command["schema_version"] == SCHEMA_VERSION, "不支持的 schema_version")
    effect = command["effect"]
    ensure(effect in {"debit", "credit", "reserve", "consume_reserved", "release_reserved"}, "未知资金动作")
    ensure(("reservation_id" in command) == (effect in {"reserve", "consume_reserved", "release_reserved"}),
           "预留相关动作必须携带 reservation_id，普通收支不得携带")
    return command


def validate_funds_receipt(command, receipt):
    validate_funds_command(command)
    fields(receipt, {"posting_key": "id", "entry_id": "id", "account_id": "id",
                     "reference_id": "id", "effect": "string", "amount_minor": "money"})
    for key in ("posting_key", "account_id", "reference_id", "effect", "amount_minor"):
        ensure(receipt[key] == command[key], f"记账回执 {key} 不匹配")
    return receipt


def validate_task_spec(spec):
    """调度只登记固定业务请求或订阅提醒，禁止保存任意函数/脚本。"""
    fields(spec, {"schema_version": "string", "task_key": "id", "trace_id": "id", "run_at": "datetime",
                  "kind": "string", "origin_action": "string", "origin_idempotency_key": "id",
                  "payload": "object"})
    ensure(spec["schema_version"] == SCHEMA_VERSION, "不支持的 schema_version")
    ensure(spec["origin_action"] in ACTIONS and ACTIONS[spec["origin_action"]]["write"], "任务必须关联原写操作")
    if spec["kind"] == "execute_request":
        request = validate_request(spec["payload"])
        ensure(request["action"] in {"transfer.create", "merchant.order"}, "此请求不能登记为到期执行任务")
        expected_origin = "transfer.schedule" if request["action"] == "transfer.create" else "budget.reserve"
        ensure(spec["origin_action"] == expected_origin, "调度来源与执行动作不符")
        ensure(request["trace_id"] == spec["trace_id"], "调度与执行必须共用 trace_id")
        ensure(request["idempotency_key"] != spec["origin_idempotency_key"], "调度登记与到期执行必须用独立幂等键")
    elif spec["kind"] == "notify_subscription":
        fields(spec["payload"], {"subscription_id": "id"})
        ensure(spec["origin_action"] == "subscription.remind", "提醒任务必须关联 subscription.remind")
    else:
        ensure(False, "未知调度任务类型")
    return spec


TASK_STATES = {"scheduled", "running", "dispatched", "failed", "unknown", "cancelled"}


def validate_task(task):
    fields(task, {"task_id": "id", "state": "string", "spec": "object"})
    validate_task_spec(task["spec"])
    ensure(task["state"] in TASK_STATES, "未知调度状态")
    return task


def validate_task_transition(previous, current):
    validate_task(previous)
    validate_task(current)
    ensure(previous["task_id"] == current["task_id"] and previous["spec"] == current["spec"], "任务身份和内容不可改写")
    if previous == current:
        return current
    allowed = {"scheduled": {"running", "cancelled"}, "running": {"dispatched", "failed", "unknown"},
               "unknown": {"dispatched", "failed"}}
    ensure(current["state"] in allowed.get(previous["state"], set()), "非法调度状态变化")
    return current


def validate_card_change(action, previous, current):
    check_value(previous, "enum:card_state", "previous")
    check_value(current, "enum:card_state", "current")
    check_value(action, "string", "action")
    rules = {"card.freeze": ({"active"}, "frozen"), "card.unfreeze": ({"frozen"}, "active"),
             "card.report_loss": ({"active", "frozen"}, "lost"), "card.release_loss": ({"lost"}, "frozen")}
    ensure(action in rules, "未知卡片状态操作")
    origins, target = rules[action]
    ensure(previous in origins and current == target, "当前卡状态不允许此变更；幂等重试应先重放原回执")
    return current


def validate_subscription_change(request, previous, current):
    validate_request(request)
    check_value(previous, "enum:subscription_state", "previous")
    check_value(current, "enum:subscription_state", "current")
    if request["action"] == "subscription.review":
        target = "active" if request["params"]["decision"] == "accept" else "dismissed"
        ensure(previous == "candidate" and current == target, "只能核实候选订阅")
    else:
        ensure(request["action"] == "subscription.cancel" and previous == "active" and current == "cancelled",
               "只能取消已核实的订阅；候选排除不等于取消代扣")
    return current


class BankServices(Protocol):
    """B 实现此内部约定；principal_id 来自登录会话，transaction 来自网关。

    每个方法均必须检查身份和资源归属，失败抛 ServiceError。
    transaction 内所有记账、订单、幂等结果和审计必须原子提交。
    """

    def get_context(self, *, principal_id: str) -> dict: ...

    def post_funds(self, transaction: object, command: dict, *, principal_id: str) -> dict: ...

    def register_task(self, transaction: object, spec: dict, *, principal_id: str) -> dict: ...

    def get_task(self, task_id: str, *, principal_id: str) -> dict: ...

    def cancel_task(self, transaction: object, task_id: str, *, principal_id: str) -> dict: ...
