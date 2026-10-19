"""公开 `subscription.charge`：每期续费独立强验证，不做后台自动扣款。"""

from calendar import monthrange
from datetime import date, timedelta, timezone
from uuid import uuid4

from shared import ServiceError
from .store import utc_timestamp

ACTIONS = frozenset({"subscription.charge"})
SHANGHAI = timezone(timedelta(hours=8))


def _today(now):
    return now.astimezone(SHANGHAI).date()


def _add_months(anchor, months):
    total = anchor.month - 1 + months
    year, month = anchor.year + total // 12, total % 12 + 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def _next(day, interval):
    return day + timedelta(days=7) if interval == "weekly" else _add_months(day, 1 if interval == "monthly" else 12)


def _row(c, subscription_id, principal):
    row = c.execute("""SELECT s.*, a.available_minor, a.principal_id AS owner
        FROM subscriptions s JOIN accounts a USING(account_id)
        WHERE s.subscription_id=? AND a.principal_id=?""", (subscription_id, principal)).fetchone()
    if row is None:
        raise ServiceError("FORBIDDEN", "订阅不存在或不属于当前用户。")
    return row


def binding(c, request, principal, now):
    row = _row(c, request["params"]["subscription_id"], principal)
    if row["subscription_state"] != "active":
        raise ServiceError("INVALID_STATE", "只有 active 订阅可以续费。")
    if date.fromisoformat(row["next_charge_date"]) > _today(now):
        raise ServiceError("INVALID_STATE", "本期尚未到期。")
    if row["available_minor"] < row["amount_minor"]:
        raise ServiceError("INSUFFICIENT_FUNDS", "可用余额不足。")
    return [row["subscription_id"], row["next_charge_date"], row["amount_minor"]]


def apply(store, c, request, principal, now):
    store.require_write_transaction(c)
    row = _row(c, request["params"]["subscription_id"], principal)
    binding(c, request, principal, now)
    charge_day = row["next_charge_date"]
    entry_id = "renew_" + uuid4().hex
    occurred = utc_timestamp(now.isoformat())
    c.execute("UPDATE accounts SET balance_minor=balance_minor-?, available_minor=available_minor-? WHERE account_id=?",
              (row["amount_minor"], row["amount_minor"], row["account_id"]))
    c.execute("""INSERT INTO ledger_entries
        (transaction_id,account_id,reference_id,posting_key,amount_minor,direction,kind,category,occurred_at,merchant_name)
        VALUES (?,?,?,?,?, 'debit','subscription','订阅',?,?)""",
        (entry_id, row["account_id"], row["subscription_id"], entry_id, row["amount_minor"],
         occurred, row["merchant_name"]))
    try:
        c.execute("""INSERT INTO subscription_charges
            (charge_id, principal_id, subscription_id, charge_date, amount_minor, entry_id, operation_key, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            ("charge_" + uuid4().hex, principal, row["subscription_id"], charge_day,
             row["amount_minor"], entry_id, request["idempotency_key"], occurred))
    except Exception as exc:  # 同期已扣过（含换键重试）；保存点会回滚流水与扣款
        raise ServiceError("INVALID_STATE", "本期已扣款，请勿重复提交。") from exc
    next_day = _next(date.fromisoformat(charge_day), row["interval"])
    c.execute("UPDATE subscriptions SET next_charge_date=?, revision=revision+1 WHERE subscription_id=?",
              (next_day.isoformat(), row["subscription_id"]))
    return (dict(subscription_id=row["subscription_id"], charged_minor=row["amount_minor"],
                 next_charge_date=next_day.isoformat()),
            dict(subscription_id=row["subscription_id"], amount_minor=row["amount_minor"],
                 charge_date=charge_day))
