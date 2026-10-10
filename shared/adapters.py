"""旧字段 → 统一契约；统一状态 → 用户文案。拒绝猜测金额单位。"""

from copy import deepcopy
import json
import re

from .contracts import (ContractError, MAX_JSON_BYTES, MAX_SAFE_INTEGER, SCHEMA_VERSION,
                        STATUSES, ensure, json_value, validate_request, validate_result)

# 仅在接入边缘兼容；成员模块内部全部使用标准字段名。
FIELD_ALIASES = {
    "accountId": "account_id", "payeeId": "payee_id", "recipient_id": "payee_id",
    "productId": "product_id", "cardId": "card_id", "subscriptionId": "subscription_id",
    "startDate": "start_date", "endDate": "end_date", "executeAt": "execute_at",
    "remark": "memo", "amountFen": "amount_minor", "amount_fen": "amount_minor", "amountMinor": "amount_minor",
    "payeeIds": "payee_ids", "productIds": "product_ids", "holdingId": "holding_id",
    "riskProfileId": "risk_profile_id", "remindAt": "remind_at", "limitMinor": "limit_minor",
    "reservationId": "reservation_id", "itemId": "item_id", "deliverOn": "deliver_on",
    "deliveryAddressId": "delivery_address_id", "questionnaireVersion": "questionnaire_version",
}


def yuan_to_minor(value):
    """元仅接受十进制字符串/整数；不接收已有二进制精度损失的 float。"""
    ensure(type(value) in (str, int), "元金额请传字符串，例如 '12.50'，不能传 float/bool")
    if type(value) is int:
        ensure(0 <= value <= MAX_SAFE_INTEGER // 100, "元金额超出范围")
    value = str(value)
    ensure(len(value) <= 17 and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", value) is not None,
           "元金额必须是非负十进制，最多两位小数")
    whole, _, fraction = value.partition(".")
    amount = int(whole) * 100 + int(fraction.ljust(2, "0"))
    ensure(amount <= MAX_SAFE_INTEGER, "金额超出 JSON 安全整数范围")
    return amount


def adapt_request(action, raw_params, *, request_id, trace_id, idempotency_key=None):
    ensure(type(raw_params) is dict, "raw_params 必须是对象")
    json_value(raw_params)
    params = {}
    for old, value in raw_params.items():
        ensure(type(old) is str, "字段名必须是字符串")
        ensure(old != "amount", "amount 单位不明确，请显式使用 amount_yuan 或 amount_minor")
        target = "amount_minor" if old == "amount_yuan" else FIELD_ALIASES.get(old, old)
        ensure(target not in params, f"字段冲突: {target}，不能同时传多个别名")
        params[target] = yuan_to_minor(value) if old == "amount_yuan" else deepcopy(value)
    request = dict(schema_version=SCHEMA_VERSION, request_id=request_id, trace_id=trace_id,
                   action=action, params=params)
    if idempotency_key is not None:
        request["idempotency_key"] = idempotency_key
    return validate_request(request)


def adapt_result(request, raw_result, *, source, status_map=None):
    """供可信后端适配器调用；状态映射由程序员配置，不能由用户/模型传入。"""
    validate_request(request)
    ensure(type(raw_result) is dict, "模块返回值必须是对象")
    json_value(raw_result)
    metadata = ("schema_version", "request_id", "trace_id", "action")
    ensure(set(raw_result) <= {"status", "data", "error", "next_action", *metadata}, "模块返回了未知字段")
    for field in metadata:
        if field in raw_result:
            ensure(raw_result[field] == request[field], f"后端返回的 {field} 与请求不匹配")
    if status_map is None:
        status_map = {status: status for status in STATUSES}
    ensure(type(status_map) is dict and all(type(k) is str and type(v) is str and v in STATUSES
                                          for k, v in status_map.items()), "status_map 配置无效")
    raw_status = raw_result.get("status")
    ensure(type(raw_status) is str and raw_status in status_map, "未配置此模块状态映射")
    result = {name: request[name] for name in metadata}
    result.update(status=status_map[raw_status], source=source, data=deepcopy(raw_result.get("data", {})))
    if "error" in raw_result:
        result["error"] = deepcopy(raw_result["error"])
    if "next_action" in raw_result:
        result["next_action"] = deepcopy(raw_result["next_action"])
    return validate_result(request, result)


def strict_json_loads(text):
    """网络/文件入口先用此函数；普通 json.loads 会静默覆盖重复键。"""
    ensure(type(text) is str, "JSON 输入必须是 UTF-8 解码后的字符串")

    def pairs(items):
        result = {}
        for key, value in items:
            ensure(key not in result, "JSON 包含重复字段")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ContractError("JSON 不接受 NaN/Infinity")

    try:
        ensure(len(text.encode("utf-8")) <= MAX_JSON_BYTES, "JSON 超过 256 KiB")
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
        json_value(value)
        return value
    except ContractError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ContractError("JSON 格式无效") from exc


def loads_request(text):
    return validate_request(strict_json_loads(text))


def loads_result(request, text):
    """网络响应必须完整携带关联字段；不为外部响应猜测 request_id。"""
    return validate_result(request, strict_json_loads(text))


def result_text(request, result):
    """页面与智能体共用此文案；不把后端任意字符串当作交易成功的证据。"""
    validate_result(request, result)
    prefix = "【模拟】" if result["source"] == "sandbox" else ""
    if result["status"] == "unknown":
        return prefix + "结果待核实，请查询原操作状态，暂勿重新提交。"
    if result["status"] == "succeeded":
        if request["action"] == "operation.get":
            original = result["data"]
            text = result_text(original["operation_request"], original["operation_result"])
            return prefix + "原操作查询：" + text.removeprefix(prefix)
        specific = {
            "transfer.create": "转账已完成。", "transfer.split": "AA 收款单已创建，尚不代表已收款。",
            "card.apply": "虚拟卡申请已提交，尚不代表已获批。",
            "scenario.plan": "计划已生成，尚未执行其中的业务操作。",
            "budget.reserve": "预算已预留。", "budget.release": "预算预留已释放。",
            "wealth.purchase": "申购订单已提交，份额确认以订单状态为准。",
            "wealth.redeem": "赎回订单已提交，到账以订单状态为准。",
            "merchant.order": "商户订单已提交，配送状态以订单为准。",
            "wealth.assess": "风险评估已保存。", "wealth.compare": "产品对比已完成。",
            "bill.analyze": "账单分析已完成。", "subscription.cancel": "订阅已取消。",
            "subscription.remind": "提醒已登记，尚未发送。",
            "card.freeze": "卡片已冻结。", "card.unfreeze": "卡片已解冻。",
            "card.report_loss": "卡片已挂失。",
        }
        return prefix + specific.get(request["action"], "操作已完成。")
    return prefix + STATUSES[result["status"]] + "。"
