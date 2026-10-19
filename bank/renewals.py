"""半自动订阅续费：只由可信页面按钮调用，不是模型动作，不新增公开契约。

B/E 已联签：不做后台自动扣款、不做长期免密授权；每期由用户在场触发，
直接扣减可用余额并记一条 `kind=subscription` 流水。幂等由
`ledger_entries.posting_key = renew_<subscription_id>_<charge_date>` 保证，
同一期不会重复扣款。扣款成功才推进 next_charge_date。
"""

from datetime import date, timedelta, timezone
from calendar import monthrange

from shared import ServiceError
from .services import owned_account
from .store import utc_timestamp

SHANGHAI = timezone(timedelta(hours=8))


def _today(now):
    return now.astimezone(SHANGHAI).date()


def _add_months(anchor, months):
    total = anchor.month - 1 + months
    year, month = anchor.year + total // 12, total % 12 + 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def _advance(day, interval):
    if interval == "weekly":
        return day + timedelta(days=7)
    if interval == "monthly":
        return _add_months(day, 1)
    return _add_months(day, 12)


def due(c, principal, now):
    """返回当前用户已到期、可续费的 active 订阅。"""
    today = _today(now)
    rows = c.execute("""SELECT s.* FROM subscriptions s JOIN accounts a USING(account_id)
        WHERE a.principal_id=? AND s.subscription_state='active' ORDER BY s.subscription_id""",
        (principal,)).fetchall()
    return [dict(row) for row in rows if date.fromisoformat(row["next_charge_date"]) <= today]


def charge(store, c, subscription_id, principal, now, key):
    """扣当期费用并推进下一扣费日；同一 key 重放原结果。"""
    store.require_write_transaction(c)
    row = c.execute("""SELECT s.*, a.available_minor FROM subscriptions s JOIN accounts a USING(account_id)
        WHERE s.subscription_id=? AND a.principal_id=?""", (subscription_id, principal)).fetchone()
    if row is None:
        raise ServiceError("FORBIDDEN", "订阅不存在或不属于当前用户。")
    if row["subscription_state"] != "active":
        raise ServiceError("INVALID_STATE", "只有 active 订阅可以续费。")
    posting = f"renew_{key}"
    saved = c.execute("SELECT * FROM ledger_entries WHERE posting_key=?", (posting,)).fetchone()
    if saved is not None:
        return dict(subscription_id=subscription_id, charged_minor=saved["amount_minor"],
                    next_charge_date=row["next_charge_date"], replayed=True)
    charge_day = date.fromisoformat(row["next_charge_date"])
    if charge_day > _today(now):
        raise ServiceError("INVALID_STATE", "本期尚未到期。")
    if row["available_minor"] < row["amount_minor"]:
        raise ServiceError("INSUFFICIENT_FUNDS", "可用余额不足，本期未扣款。")
    entry_id = posting
    c.execute("UPDATE accounts SET balance_minor=balance_minor-?, available_minor=available_minor-? WHERE account_id=?",
              (row["amount_minor"], row["amount_minor"], row["account_id"]))
    c.execute("""INSERT INTO ledger_entries
        (transaction_id,account_id,reference_id,posting_key,amount_minor,direction,kind,category,occurred_at,merchant_name)
        VALUES (?,?,?,?,?, 'debit','subscription','订阅',?,?)""",
        (entry_id, row["account_id"], subscription_id, posting, row["amount_minor"],
         utc_timestamp(now.isoformat()), row["merchant_name"]))
    next_day = _advance(charge_day, row["interval"])
    c.execute("UPDATE subscriptions SET next_charge_date=?, revision=revision+1 WHERE subscription_id=?",
              (next_day.isoformat(), subscription_id))
    c.execute("""INSERT INTO audit_events (principal_id,trace_id,action,outcome,occurred_at,details_json)
        VALUES (?,?,?,'succeeded',?,?)""",
        (principal, "renew_" + entry_id, "subscription.renew", utc_timestamp(now.isoformat()),
         '{"subscription_id":"%s","amount_minor":%d}' % (subscription_id, row["amount_minor"])))
    return dict(subscription_id=subscription_id, charged_minor=row["amount_minor"],
                next_charge_date=next_day.isoformat(), replayed=False)
