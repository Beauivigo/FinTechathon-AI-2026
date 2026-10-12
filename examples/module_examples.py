"""共享契约样例，不是业务实现。--write 更新供各语言共用的 contracts.json。"""

import argparse
from copy import deepcopy
import json
from pathlib import Path

from shared import adapt_request, adapt_result, operation_digest, result_text


def build_examples():
    time = "2026-10-12T09:00:00+08:00"
    account = {"account_id": "account_1"}
    transfer = account | {"payee_id": "payee_1", "amount_minor": 1250}
    period = account | {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    product = {"product_id": "product_1", "name": "模拟产品一", "risk_level": "R2",
               "minimum_amount_minor": 10000, "lock_days": 30,
               "fee_description": "模拟申购费为零", "redemption_rule": "满30天后可申请赎回"}
    samples = {
        "transfer.create": (transfer, {"transaction_id": "tx_1", "amount_minor": 1250}),
        "transfer.schedule": (transfer | {"execute_at": time}, {"schedule_id": "schedule_1", "execute_at": time}),
        "transfer.split": (account | {"payee_ids": ["payee_1", "payee_2"], "amount_minor": 101},
                           {"collection_id": "collection_1", "shares": [
                               {"payee_id": "payee_1", "amount_minor": 51}, {"payee_id": "payee_2", "amount_minor": 50}]}),
        "bill.analyze": (period, {"total_expense_minor": 1250,
                                 "categories": [{"category": "餐饮", "amount_minor": 1250}],
                                 "anomalies": [{"transaction_id": "tx_1", "reason": "与另一笔消费金额及时间相近，请核实"}],
                                 "report": "本期示例支出12.50元，异常标记仅供核实。"}),
        "wealth.compare": ({"product_ids": ["product_1"]}, {"products": [product]}),
        "wealth.assess": ({"questionnaire_version": "demo_v1", "answers": {"q1": "choice_2", "q2": "choice_3"}},
                          {"risk_profile_id": "profile_1", "risk_level": "R2"}),
        "wealth.purchase": (account | {"product_id": "product_1", "risk_profile_id": "profile_1", "amount_minor": 10000},
                            {"order_id": "purchase_1", "amount_minor": 10000}),
        "wealth.redeem": (account | {"holding_id": "holding_1", "units": "12.5000"}, {"order_id": "redeem_1", "units": "12.5"}),
        "card.apply": (account, {"application_id": "application_1"}),
        "card.set_limit": ({"card_id": "card_1", "limit_minor": 100000}, {"card_id": "card_1", "limit_minor": 100000}),
        "card.freeze": ({"card_id": "card_1"}, {"card_id": "card_1", "card_state": "frozen"}),
        "card.unfreeze": ({"card_id": "card_1"}, {"card_id": "card_1", "card_state": "active"}),
        "card.report_loss": ({"card_id": "card_1"}, {"card_id": "card_1", "card_state": "lost"}),
        "card.release_loss": ({"card_id": "card_1"}, {"card_id": "card_1", "card_state": "frozen"}),
        "subscription.list": (account, {"subscriptions": [{"subscription_id": "subscription_1",
            "merchant_name": "模拟视频会员", "amount_minor": 2500, "interval": "monthly",
            "next_charge_date": "2026-11-01", "subscription_state": "candidate",
            "evidence": "同一商户连续三个月扣费25元，需用户核实。"}]}),
        "subscription.review": ({"subscription_id": "subscription_1", "decision": "accept"},
                                {"subscription_id": "subscription_1", "subscription_state": "active"}),
        "subscription.cancel": ({"subscription_id": "subscription_1"},
                                {"subscription_id": "subscription_1", "subscription_state": "cancelled"}),
        "subscription.remind": ({"subscription_id": "subscription_1", "remind_at": time}, {"reminder_id": "reminder_1"}),
        "scenario.plan": ({"goal": "为10月14日生日预留1000元，提前两天买鲜花和蛋糕"},
                          {"summary": "预留预算后，分别安排鲜花和蛋糕下单。", "steps": [
                              {"step_id": "reserve", "action": "budget.reserve", "depends_on": [], "description": "预留预算"},
                              {"step_id": "flowers", "action": "merchant.order", "depends_on": ["reserve"], "description": "安排鲜花"},
                              {"step_id": "cake", "action": "merchant.order", "depends_on": ["reserve"], "description": "安排蛋糕"}]}),
        "budget.reserve": (account | {"amount_minor": 100000, "purpose": "生日预算"},
                           {"reservation_id": "reservation_1", "amount_minor": 100000}),
        "budget.release": ({"reservation_id": "reservation_1"}, {"reservation_id": "reservation_1"}),
        "merchant.order": (account | {"reservation_id": "reservation_1", "item_id": "flowers_1", "amount_minor": 20000,
                                      "deliver_on": "2026-10-14", "delivery_address_id": "address_1"},
                           {"order_id": "merchant_order_1", "amount_minor": 20000}),
        "account.balance": (account, account | {"balance_minor": 200000, "available_minor": 100000,
                                                "reserved_minor": 100000, "currency": "CNY"}),
        "payee.resolve": ({"query": "小王", "match_type": "name"}, {"candidates": [
            {"payee_id": "payee_1", "display_name": "小王", "masked_account": "****1234"}]}),
        "transaction.list": (period | {"limit": 20}, {"transactions": [account | {
            "transaction_id": "tx_1", "amount_minor": 1250, "direction": "debit", "kind": "consumption",
            "category": "餐饮", "occurred_at": time, "merchant_name": "模拟餐厅"}], "next_cursor": None}),
    }
    from shared import ACTIONS
    examples = []
    for index, (name, (params, data)) in enumerate(samples.items(), 1):
        request = adapt_request(name, params, request_id=f"req_{index}", trace_id=f"trace_{index}",
                                idempotency_key=f"operation_{index}" if ACTIONS[name]["write"] else None)
        status = "scheduled" if name == "transfer.schedule" else "succeeded"
        result = adapt_result(request, {"status": status, "data": data}, source="sandbox")
        examples.append({"name": name, "request": request, "result": result, "display_text": result_text(request, result)})

    original = examples[0]["request"]
    digest = operation_digest(original, principal_id="demo_user_1")
    for status, kind in (("awaiting_confirmation", "confirm"), ("awaiting_verification", "verify")):
        challenge = {"challenge_id": f"challenge_{kind}", "kind": kind, "operation_digest": digest, "expires_at": time}
        if kind == "verify":
            challenge["methods"] = ["demo_otp"]
        result = adapt_result(original, {"status": status, "next_action": challenge}, source="gateway")
        examples.append({"name": f"transfer.{kind}", "request": deepcopy(original), "result": result,
                         "display_text": result_text(original, result)})
    unknown = adapt_result(original, {"status": "unknown", "error": {"code": "RESULT_UNKNOWN", "message": "请查询原操作"}}, source="sandbox")
    examples.append({"name": "transfer.unknown", "request": deepcopy(original), "result": unknown,
                     "display_text": result_text(original, unknown)})
    query = adapt_request("operation.get", {"original_action": "transfer.create", "original_idempotency_key": "operation_1"},
                          request_id="req_query", trace_id="trace_query")
    result = adapt_result(query, {"status": "succeeded", "data": {"operation_request": original, "operation_result": unknown}}, source="sandbox")
    examples.append({"name": "operation.get", "request": query, "result": result, "display_text": result_text(query, result)})
    return examples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="更新 examples/contracts.json")
    args = parser.parse_args()
    content = json.dumps(build_examples(), ensure_ascii=False, indent=2) + "\n"
    if args.write:
        path = Path(__file__).with_name("contracts.json")
        path.write_text(content, encoding="utf-8")
        print(f"Updated {path.name}")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
