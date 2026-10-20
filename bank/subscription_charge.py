"""公开 `subscription.charge`：每期续费独立强验证，不做后台自动扣款。"""

from calendar import monthrange
from datetime import date, timedelta, timezone
from uuid import uuid4
import sqlite3

from shared import ServiceError
from .store import utc_timestamp

ACTIONS = frozenset({"subscription.charge"})
SHANGHAI = timezone(timedelta(hours=8))


def _today(now):
    return now.astimezone(SHANGHAI).date()


def _add_months(anchor, months, anchor_day=None):
    total = anchor.month - 1 + months
    year, month = anchor.year + total // 12, total % 12 + 1
    return date(year, month, min(anchor_day or anchor.day, monthrange(year, month)[1]))


def _next(day, interval, anchor):
    return day + timedelta(days=7) if interval == "weekly" else _add_months(day, 1 if interval == "monthly" else 12, anchor.day)


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
    cards=c.execute('SELECT * FROM cards WHERE account_id=? ORDER BY card_id',
                    (row['account_id'],)).fetchall()
    if not cards:
        raise ServiceError('NOT_FOUND','续费账户没有支付卡。')
    active=[card for card in cards if card['card_state']=='active']
    if not active:
        raise ServiceError('INVALID_STATE','续费支付卡须为正常状态。')
    eligible=[card for card in active if card['limit_minor']>=row['amount_minor']]
    if not eligible:
        raise ServiceError('LIMIT_EXCEEDED','续费金额超过支付卡单笔限额。')
    card=eligible[0]
    # E-02既定自动选卡顺序；执行时重算并与挑战绑定比较，禁止静默换卡/换账户。
    return [row['subscription_id'],row['account_id'],row['next_charge_date'],
            row['amount_minor'],row['revision'],
            [card['card_id'],card['card_state'],card['limit_minor'],card['revision']]]


def apply(store, c, request, principal, now):
    store.require_write_transaction(c)
    row = _row(c, request["params"]["subscription_id"], principal)
    bound=binding(c, request, principal, now)
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
    except sqlite3.IntegrityError as exc:  # 同期已扣过；保存点回滚流水与扣款。
        raise ServiceError("INVALID_STATE", "本期已扣款，请勿重复提交。") from exc
    anchor=c.execute('SELECT charge_date FROM subscription_charges WHERE subscription_id=? ORDER BY charge_date LIMIT 1',
                     (row['subscription_id'],)).fetchone()[0]
    next_day = _next(date.fromisoformat(charge_day), row["interval"], date.fromisoformat(anchor))
    c.execute("UPDATE subscriptions SET next_charge_date=?, revision=revision+1 WHERE subscription_id=?",
              (next_day.isoformat(), row["subscription_id"]))
    return (dict(subscription_id=row["subscription_id"], charged_minor=row["amount_minor"],
                 next_charge_date=next_day.isoformat()),
            dict(subscription_id=row["subscription_id"], amount_minor=row["amount_minor"],
                 charge_date=charge_day, account_id=row['account_id'],card_id=bound[-1][0]))
