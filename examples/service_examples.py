"""内部协议的静态样例，不是服务实现。--write 导出 internal_contracts.json。"""

import argparse
from copy import deepcopy
import json
from pathlib import Path

from examples.module_examples import build_examples
from shared import (SCHEMA_VERSION, validate_context, validate_funds_command,
                    validate_funds_receipt, validate_task)


def build_service_examples():
    public = {row["name"]: row for row in build_examples()}
    context = validate_context({
        "schema_version": SCHEMA_VERSION, "source": "sandbox",
        "accounts": [{"account_id": "account_1", "display_name": "模拟日常账户"}],
        "products": public["wealth.compare"]["result"]["data"]["products"],
        "holdings": [{"holding_id": "holding_1", "account_id": "account_1", "product_id": "product_1", "units": "12.5"}],
        "cards": [{"card_id": "card_1", "account_id": "account_1", "card_state": "active", "limit_minor": 100000}],
        "items": [{"item_id": "flowers_1", "name": "模拟鲜花", "price_minor": 20000}],
        "addresses": [{"delivery_address_id": "address_1", "label": "模拟地址（完整配送信息由服务端保存）"}],
        "questionnaires": [{"questionnaire_version": "demo_v1", "questions": [
            {"question_id": "q1", "text": "可承受的波动？", "choices": [
                {"choice_id": "choice_1", "text": "较低"}, {"choice_id": "choice_2", "text": "适中"}]},
            {"question_id": "q2", "text": "计划持有多久？", "choices": [
                {"choice_id": "choice_1", "text": "一个月内"}, {"choice_id": "choice_3", "text": "一年以上"}]}]}],
    })
    funds = []
    for effect in ("debit", "credit", "reserve", "consume_reserved", "release_reserved"):
        command = {"schema_version": SCHEMA_VERSION, "posting_key": f"posting_{effect}", "trace_id": "trace_funds",
                   "account_id": "account_1", "reference_id": "order_1", "effect": effect,
                   "amount_minor": 100, "kind": "wealth"}
        if effect in {"reserve", "consume_reserved", "release_reserved"}:
            command["reservation_id"] = "reservation_1"
        validate_funds_command(command)
        receipt = {key: command[key] for key in ("posting_key", "account_id", "reference_id", "effect", "amount_minor")}
        receipt["entry_id"] = f"entry_{effect}"
        validate_funds_receipt(command, receipt)
        funds.append({"command": command, "receipt": receipt})
    tasks = []
    for name, origin in (("transfer.create", "transfer.schedule"), ("merchant.order", "budget.reserve")):
        request = deepcopy(public[name]["request"])
        task = {"task_id": f"task_{name}", "state": "scheduled", "spec": {
            "schema_version": SCHEMA_VERSION, "task_key": f"task_key_{name}", "trace_id": request["trace_id"],
            "run_at": "2026-10-12T09:00:00+08:00", "kind": "execute_request",
            "origin_action": origin, "origin_idempotency_key": f"origin_{name}", "payload": request}}
        tasks.append(validate_task(task))
    tasks.append(validate_task({"task_id": "task_reminder", "state": "scheduled", "spec": {
        "schema_version": SCHEMA_VERSION, "task_key": "task_key_reminder", "trace_id": "trace_reminder",
        "run_at": "2026-10-12T09:00:00+08:00", "kind": "notify_subscription", "origin_action": "subscription.remind",
        "origin_idempotency_key": "origin_reminder", "payload": {"subscription_id": "subscription_1"}}}))
    return {"context": context, "funds": funds, "tasks": tasks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build_service_examples(), ensure_ascii=False, indent=2) + "\n"
    if args.write:
        Path(__file__).with_name("internal_contracts.json").write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
