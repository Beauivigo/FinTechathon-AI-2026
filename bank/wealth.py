"""D 理财：风险评估、申购、赎回。

所有资金与持仓写入都在网关的同一串行事务/保存点内完成；本模块不自行提交，
也不接受模型直接调用。金额用整数分，费用与份额用 Decimal 四舍五入。
风险、起购、余额、锁定与份额校验同时放在 binding 中，确保在确认/强验证前
就能拦截；apply 内再校验一次，防止确认期间状态变化。
"""

from datetime import date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from uuid import uuid4

from wealth import catalog, questionnaire

from shared import ServiceError
from .services import owned_account
from .store import utc_timestamp

ACTIONS = frozenset({"wealth.assess", "wealth.purchase", "wealth.redeem"})

SHANGHAI = timezone(timedelta(hours=8))
RISK_RANK = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}
UNIT_QUANT = Decimal("0.0001")
CENT = Decimal("1")


def _money(value):
    return int(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _units(net_minor, net_value_minor):
    return (Decimal(net_minor) / Decimal(net_value_minor)).quantize(UNIT_QUANT, rounding=ROUND_HALF_UP)


def _format_units(value):
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _fee(amount_minor, bps):
    return _money(Decimal(amount_minor) * Decimal(bps) / Decimal(10000))


def _today(now):
    return now.astimezone(SHANGHAI).date()


def _profile(c, profile_id, principal):
    row = c.execute("SELECT * FROM risk_profiles WHERE profile_id=? AND principal_id=?",
                    (profile_id, principal)).fetchone()
    if row is None:
        raise ServiceError("FORBIDDEN", "风险档案不存在或不属于当前用户。")
    return row


def _holding(c, holding_id, principal):
    row = c.execute("SELECT * FROM holdings WHERE holding_id=? AND principal_id=?",
                    (holding_id, principal)).fetchone()
    if row is None:
        raise ServiceError("FORBIDDEN", "持仓不存在或不属于当前用户。")
    return row


def _account(c, account_id, principal):
    return owned_account(c, account_id, principal)


def _purchase_state(c, p, principal):
    """产品/风险/起购/余额校验，返回产品、档案与账户。"""
    product = catalog.product(p["product_id"])
    if product is None:
        raise ServiceError("NOT_FOUND", "产品不存在。")
    profile = _profile(c, p["risk_profile_id"], principal)
    if RISK_RANK[profile["risk_level"]] < RISK_RANK[product["risk_level"]]:
        raise ServiceError("RISK_BLOCKED", "风险等级不匹配，不能申购该产品。")
    if p["amount_minor"] < product["minimum_amount_minor"]:
        raise ServiceError("LIMIT_EXCEEDED", "低于产品起购金额。")
    account = _account(c, p["account_id"], principal)
    if account["available_minor"] < p["amount_minor"]:
        raise ServiceError("INSUFFICIENT_FUNDS", "可用余额不足。")
    return product, profile, account


def _redeem_state(c, p, principal, today):
    """持仓/锁定/份额校验，返回持仓、产品与赎回份额。"""
    holding = _holding(c, p["holding_id"], principal)
    if holding["account_id"] != p["account_id"]:
        raise ServiceError("FORBIDDEN", "持仓不属于该账户。")
    if holding["state"] != "open":
        raise ServiceError("INVALID_STATE", "持仓已结清，不能赎回。")
    product = catalog.product(holding["product_id"])
    if product is None:
        raise ServiceError("NOT_FOUND", "产品不存在。")
    confirmed_on = date.fromisoformat(holding["confirmed_on"])
    if confirmed_on + timedelta(days=holding["lock_days"]) > today:
        raise ServiceError("INVALID_STATE", "锁定期内不可赎回。")
    held = Decimal(holding["units"])
    units = Decimal(p["units"])
    if units <= 0 or units > held:
        raise ServiceError("INVALID_STATE", "可赎回份额不足。")
    return holding, product, units


def binding(c, request, principal, now):
    """校验归属并返回稳定绑定；请求前、确认时都会重跑。"""
    p = request["params"]
    if request["action"] == "wealth.assess":
        return [principal]
    _account(c, p["account_id"], principal)
    if request["action"] == "wealth.purchase":
        product, profile, _ = _purchase_state(c, p, principal)
        return [p["account_id"], p["product_id"], profile["profile_id"], profile["risk_level"]]
    holding = _holding(c, p["holding_id"], principal)
    _redeem_state(c, p, principal, _today(now))
    return [p["account_id"], p["holding_id"], holding["units"]]


def _assess(c, request, principal, now):
    p = request["params"]
    total, level = questionnaire.score(p["answers"], p["questionnaire_version"])
    profile_id = "profile_" + uuid4().hex
    c.execute("""INSERT INTO risk_profiles
        (profile_id, principal_id, questionnaire_version, answers_json, total_score, risk_level, created_at)
        VALUES (?,?,?,?,?,?,?)""",
        (profile_id, principal, p["questionnaire_version"],
         json.dumps(p["answers"], ensure_ascii=False, sort_keys=True),
         total, level, utc_timestamp(now.isoformat())))
    return {"risk_profile_id": profile_id, "risk_level": level}, {"profile_id": profile_id, "total_score": total}


def _purchase(c, request, principal, now):
    p = request["params"]
    product, profile, account = _purchase_state(c, p, principal)
    amount = p["amount_minor"]
    fee = _fee(amount, product["purchase_fee_bps"])
    net = amount - fee
    units = _units(net, product["net_value_minor"])
    if units <= 0:
        raise ServiceError("LIMIT_EXCEEDED", "净申购金额不足以形成份额。")
    order_id = "order_" + uuid4().hex
    holding_id = "holding_" + uuid4().hex
    entry_id = "wealth_" + uuid4().hex
    occurred_at = utc_timestamp(now.isoformat())
    c.execute("UPDATE accounts SET balance_minor=balance_minor-?, available_minor=available_minor-? WHERE account_id=?",
              (amount, amount, p["account_id"]))
    c.execute("""INSERT INTO ledger_entries
        (transaction_id,account_id,reference_id,posting_key,amount_minor,direction,kind,category,occurred_at,merchant_name)
        VALUES (?,?,?,?,?, 'debit','wealth','理财',?,?)""",
        (entry_id, p["account_id"], order_id, entry_id, amount, occurred_at, product["name"]))
    c.execute("""INSERT INTO holdings
        (holding_id, principal_id, account_id, product_id, units, confirmed_on, lock_days, state)
        VALUES (?,?,?,?,?,?,?,'open')""",
        (holding_id, principal, p["account_id"], p["product_id"], format(units, "f"),
         _today(now).isoformat(), product["lock_days"]))
    c.execute("""INSERT INTO wealth_orders
        (order_id, principal_id, account_id, product_id, order_type, amount_minor, fee_minor,
         units, holding_id, status, operation_key, occurred_at)
        VALUES (?,?,?,?, 'purchase', ?,?,?,?, 'succeeded', ?,?)""",
        (order_id, principal, p["account_id"], p["product_id"], amount, fee,
         _format_units(units), holding_id, request["idempotency_key"], occurred_at))
    return ({"order_id": order_id, "amount_minor": amount},
            {"order_id": order_id, "amount_minor": amount, "fee_minor": fee, "units": _format_units(units)})


def _redeem(c, request, principal, now):
    p = request["params"]
    holding, product, units = _redeem_state(c, p, principal, _today(now))
    gross = _money(units * Decimal(product["net_value_minor"]))
    fee = _fee(gross, product["redeem_fee_bps"])
    net = gross - fee
    remaining = Decimal(holding["units"]) - units
    order_id = "order_" + uuid4().hex
    entry_id = "wealth_" + uuid4().hex
    occurred_at = utc_timestamp(now.isoformat())
    c.execute("UPDATE holdings SET units=?, state=? WHERE holding_id=?",
              (format(remaining, "f"), "closed" if remaining == 0 else "open", p["holding_id"]))
    c.execute("UPDATE accounts SET balance_minor=balance_minor+?, available_minor=available_minor+? WHERE account_id=?",
              (net, net, p["account_id"]))
    c.execute("""INSERT INTO ledger_entries
        (transaction_id,account_id,reference_id,posting_key,amount_minor,direction,kind,category,occurred_at,merchant_name)
        VALUES (?,?,?,?,?, 'credit','wealth','理财',?,?)""",
        (entry_id, p["account_id"], order_id, entry_id, net, occurred_at, product["name"]))
    c.execute("""INSERT INTO wealth_orders
        (order_id, principal_id, account_id, product_id, order_type, amount_minor, fee_minor,
         units, holding_id, status, operation_key, occurred_at)
        VALUES (?,?,?,?, 'redeem', ?,?,?,?, 'succeeded', ?,?)""",
        (order_id, principal, p["account_id"], holding["product_id"], gross, fee,
         _format_units(units), p["holding_id"], request["idempotency_key"], occurred_at))
    return ({"order_id": order_id, "units": _format_units(units)},
            {"order_id": order_id, "gross_minor": gross, "fee_minor": fee, "net_minor": net})


def apply(store, c, request, principal, now):
    """调用者必须已完成授权；回执与审计由网关在同一保存点写入。"""
    store.require_write_transaction(c)
    binding(c, request, principal, now)
    if request["action"] == "wealth.assess":
        return _assess(c, request, principal, now)
    if request["action"] == "wealth.purchase":
        return _purchase(c, request, principal, now)
    return _redeem(c, request, principal, now)
