"""只读账单计算。调用方负责账户授权并提供完整、一致的流水快照。"""

from datetime import datetime, timedelta, timezone

from shared.contracts import ACTIONS, MAX_SAFE_INTEGER, RECORDS, ContractError, fields
from shared.services import ServiceError

LARGE_SINGLE_MINOR = 500_000
LARGE_SINGLE_KINDS = frozenset({"consumption", "subscription", "transfer", "wealth"})


def _money(minor):
    """整数分转两位小数字符串；不经过 float。"""
    return f"{minor // 100}.{minor % 100:02d}"


def analyze(params, transactions):
    """返回 bill.analyze 的 data；不读取数据库、不授权、不执行异常检测。

    transactions 可为迭代器，元素为标准 transaction 记录，不受分页的
    100 条上限约束。调用方不能把单页当成完整快照；本函数无法检测缺页。
    请求错误为 INVALID_REQUEST，坏流水为 UPSTREAM_ERROR，聚合超限为
    LIMIT_EXCEEDED。失败时不返回部分报告。
    """
    spec = ACTIONS["bill.analyze"]
    try:
        fields(params, spec["required"], spec["optional"])
        if params["start_date"] > params["end_date"]:
            raise ContractError("start_date 不能晚于 end_date")
    except ContractError as exc:
        raise ServiceError("INVALID_REQUEST", str(exc)) from exc

    total = refunds = 0
    categories, seen, anomalies = {}, set(), []
    shanghai = timezone(timedelta(hours=8))
    try:
        for row in transactions:
            fields(row, *RECORDS["transaction"])
            if row["transaction_id"] in seen:
                raise ContractError("流水编号重复，无法生成可靠账单")
            seen.add(row["transaction_id"])
            day = datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))
            day = day.astimezone(shanghai).date().isoformat()
            if (row["account_id"] != params["account_id"]
                    or not params["start_date"] <= day <= params["end_date"]):
                continue
            amount = row["amount_minor"]
            if (row["direction"] == "debit" and row["kind"] in LARGE_SINGLE_KINDS
                    and amount >= LARGE_SINGLE_MINOR):
                anomalies.append(dict(
                    transaction_id=row["transaction_id"],
                    reason=(f"大额单笔 {_money(amount)} 元 ≥ {_money(LARGE_SINGLE_MINOR)} 元"
                            "（C1-003-R03 分支A）")))
            if row["direction"] == "debit" and row["kind"] in {"consumption", "subscription"}:
                total += amount
                category = row["category"]
                categories[category] = categories.get(category, 0) + amount
            elif row["direction"] == "credit" and row["kind"] == "refund":
                refunds += amount
            if max(total, refunds) > MAX_SAFE_INTEGER or len(categories) > 100:
                raise ServiceError("LIMIT_EXCEEDED", "账单聚合金额或分类数量超出共享接口范围")
    except (ContractError, TypeError) as exc:
        raise ServiceError("UPSTREAM_ERROR", "流水快照格式错误或包含重复记录") from exc

    # 只用整数格式化金额，避免大金额转换为 float 后丢失分位。
    report = (
        f"上海日期 {params['start_date']} 至 {params['end_date']}（含两端），"
        f"消费/订阅毛支出 {total // 100}.{total % 100:02d} 元。"
        f"本期退款入账 {refunds // 100}.{refunds % 100:02d} 元，另列且不冲减毛支出；"
        "不推断退款原订单或归属月份。收入、转账、理财不计入毛支出。"
        "已启用大额单笔提示（单笔 ≥5000.00 元，C1-003-R03 分支A）；"
        "借贷配对、跨期退款、理财收益率等其余规则尚未启用，空列表不代表没有异常。"
    )
    anomalies.sort(key=lambda item: item["transaction_id"])
    data = dict(total_expense_minor=total,
                categories=[dict(category=name, amount_minor=categories[name])
                            for name in sorted(categories)],
                anomalies=anomalies, report=report)
    try:
        fields(data, spec["output"])
    except ContractError as exc:
        raise ServiceError("LIMIT_EXCEEDED", "账单输出超过共享接口容量") from exc
    return data
