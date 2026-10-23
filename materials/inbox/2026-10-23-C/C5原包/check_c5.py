# -*- coding: utf-8 -*-
"""C 第五轮组 3 理财联合验收 + 入口矩阵 + G01—G15 覆盖表自查。

在仓库根目录运行：
    py -X utf8 materials/inbox/2026-10-21-C/check_c5.py

可用 C5_TMP 指定临时库父目录（沙箱环境用）；不设置时使用系统临时目录。
只写临时 SQLite，不触碰 runtime/ 日常演示库。
"""
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

PKG = Path(__file__).resolve().parent


def find_repo_root(start):
    for candidate in [start, *start.parents]:
        if (candidate / "bank/gateway.py").is_file() and (candidate / "shared/contracts.py").is_file():
            return candidate
    env = os.environ.get("C5_REPO_ROOT", "").strip()
    if env and (Path(env) / "bank/gateway.py").is_file():
        return Path(env)
    return None


ROOT = find_repo_root(PKG) or find_repo_root(Path.cwd())
if ROOT is None:
    print("无法定位仓库根目录：请把本包放回 materials/inbox/2026-10-21-C，"
          "或在仓库根目录运行，或设置 C5_REPO_ROOT。")
    raise SystemExit(2)
sys.path.insert(0, str(ROOT))

from agent.routing import route  # noqa: E402
from bank.gateway import BankGateway  # noqa: E402
from bank.seed import initialize  # noqa: E402
from bank.store import Store  # noqa: E402
from shared import adapt_request  # noqa: E402
from shared.contracts import validate_result  # noqa: E402
from wealth import questionnaire  # noqa: E402

SHANGHAI = timezone(timedelta(hours=8))
LETTERS = {1: "a", 2: "b", 3: "c", 4: "d"}
P = "demo_user_a"
ACC = "demo_account_a"
failures = []
records = []


def fail(scope, message):
    failures.append({"scope": scope, "message": message})


def ans(scores):
    return {f"q{i}": f"q{i}_{LETTERS[s]}" for i, s in enumerate(scores, 1)}


def record(cid, title, expected, actual, notes=""):
    match = bool(expected == actual)
    if not match:
        fail(cid, f"expected != actual: {json.dumps(expected, ensure_ascii=False)} vs {json.dumps(actual, ensure_ascii=False)}")
    records.append(dict(id=cid, title=title, expected=expected, actual=actual, match=match, notes=notes))
    return actual


# ---------------------------------------------------------------- temp store
base_dir = Path(os.environ["C5_TMP"]) if os.environ.get("C5_TMP") else Path(tempfile.mkdtemp(prefix="c5_"))
base_dir.mkdir(parents=True, exist_ok=True)
db_path = base_dir / f"c5_{uuid4().hex}.sqlite3"
store = Store(db_path)
initialize(store)
now = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
gateway = BankGateway(store, clock=lambda: now)


def assess(scores, version="wpq_v1"):
    request = adapt_request("wealth.assess", {"questionnaire_version": version, "answers": ans(scores)},
                            request_id="a_" + uuid4().hex, trace_id="t", idempotency_key="a_" + uuid4().hex)
    return gateway.execute(request, principal_id=P)


def purchase(profile, product, amount, key=None):
    request = adapt_request("wealth.purchase",
                            dict(account_id=ACC, product_id=product, risk_profile_id=profile, amount_minor=amount),
                            request_id="p_" + uuid4().hex, trace_id="t", idempotency_key=key or "p_" + uuid4().hex)
    first = gateway.execute(request, principal_id=P)
    return request, first


def verify(pending):
    target = pending["next_action"]
    token = gateway.issue_demo_proof(target["challenge_id"], principal_id=P)
    return gateway.authorize(dict(schema_version="1.1", challenge_id=target["challenge_id"],
                                  operation_digest=target["operation_digest"], decision="approve",
                                  proof_token=token), principal_id=P)


def redeem(holding_id, units, key=None):
    request = adapt_request("wealth.redeem", dict(account_id=ACC, holding_id=holding_id, units=units),
                            request_id="r_" + uuid4().hex, trace_id="t", idempotency_key=key or "r_" + uuid4().hex)
    first = gateway.execute(request, principal_id=P)
    if first["status"] == "awaiting_verification":
        return request, first, verify(first)
    return request, first, first


def snapshot():
    with store.connection() as c:
        account = dict(c.execute("SELECT balance_minor,available_minor,reserved_minor FROM accounts WHERE account_id=?", (ACC,)).fetchone())
        holdings = [dict(row) for row in c.execute("SELECT product_id,units,state,lock_days,confirmed_on FROM holdings ORDER BY rowid")]
        orders = [dict(row) for row in c.execute("SELECT order_type,product_id,amount_minor,fee_minor,units,status FROM wealth_orders ORDER BY rowid")]
        ledger = [dict(row) for row in c.execute("SELECT direction,amount_minor,kind FROM ledger_entries WHERE kind='wealth' ORDER BY rowid")]
        challenges = c.execute("SELECT count(*) FROM challenges").fetchone()[0]
    return dict(account=account, holdings=holdings, orders=orders, ledger=ledger, challenges=challenges)


def code_of(result):
    return (result.get("error") or {}).get("code")


def profile_ok(value):
    return isinstance(value, str) and re.fullmatch(r"profile_[0-9a-f]{32}", value) is not None


# ---------------------------------------------------------------- G3-01 assessment boundaries
boundaries = [
    ((1, 1, 1, 1, 1), 5, "R1"), ((1, 2, 1, 2, 2), 8, "R1"),
    ((2, 2, 1, 2, 2), 9, "R2"), ((3, 3, 2, 2, 2), 12, "R2"),
    ((3, 3, 3, 2, 2), 13, "R3"), ((3, 3, 3, 3, 4), 16, "R3"),
    ((4, 4, 3, 3, 3), 17, "R4"), ((4, 4, 4, 3, 3), 18, "R4"),
    ((4, 4, 4, 4, 3), 19, "R5"), ((4, 4, 4, 4, 4), 20, "R5"),
]
expected_boundaries = [dict(scores=list(s), total=t, level=l) for s, t, l in boundaries]
actual_boundaries = []
for scores, total, level in boundaries:
    actual_total, actual_level = questionnaire.score(ans(scores), "wpq_v1")
    actual_boundaries.append(dict(scores=list(scores), total=actual_total, level=actual_level))
record("G3-01", "问卷 10 个分数边界映射", expected_boundaries, actual_boundaries,
       "纯函数 questionnaire.score；5/8/9/12/13/16/17/18/19/20")

# ---------------------------------------------------------------- G3-02 invalid answers
expected_invalid = [
    dict(label="missing_q5", status="failed", code="INVALID_REQUEST"),
    dict(label="unknown_choice", status="failed", code="INVALID_REQUEST"),
    dict(label="bad_version", status="failed", code="INVALID_REQUEST"),
]
actual_invalid = []
for label, payload in [
    ("missing_q5", {"questionnaire_version": "wpq_v1", "answers": {k: v for k, v in ans((3, 3, 3, 2, 2)).items() if k != "q5"}}),
    ("unknown_choice", {"questionnaire_version": "wpq_v1", "answers": {**ans((3, 3, 3, 2, 2)), "q1": "q1_z"}}),
    ("bad_version", {"questionnaire_version": "wpq_v2", "answers": ans((3, 3, 3, 2, 2))}),
]:
    request = adapt_request("wealth.assess", payload, request_id="a_" + uuid4().hex, trace_id="t",
                            idempotency_key="a_" + uuid4().hex)
    result = gateway.execute(request, principal_id=P)
    actual_invalid.append(dict(label=label, status=result.get("status"), code=code_of(result)))
record("G3-02", "非法问卷拒绝且不落库", expected_invalid, actual_invalid)

# ---------------------------------------------------------------- G3-03 assess R3/R2
r3 = assess((3, 3, 3, 2, 2))
r3_data = r3.get("data") or {}
r2 = assess((2, 2, 1, 2, 2))
r2_data = r2.get("data") or {}
actual_profiles = {
    "r3": dict(status=r3.get("status"), risk_level=r3_data.get("risk_level"), profile_ok=profile_ok(r3_data.get("risk_profile_id"))),
    "r2": dict(status=r2.get("status"), risk_level=r2_data.get("risk_level"), profile_ok=profile_ok(r2_data.get("risk_profile_id"))),
}
record("G3-03", "风险评估 R3/R2 保存档案", {
    "r3": dict(status="succeeded", risk_level="R3", profile_ok=True),
    "r2": dict(status="succeeded", risk_level="R2", profile_ok=True),
}, actual_profiles)
r3_profile = r3_data.get("risk_profile_id")
r2_profile = r2_data.get("risk_profile_id")

# ---------------------------------------------------------------- G3-04 risk mismatch
_, mismatch = purchase(r2_profile, "WP-C04", 100000)
actual_mismatch = dict(status=mismatch.get("status"), code=code_of(mismatch), snapshot=snapshot())
record("G3-04", "R2 用户申购 R4 产品被拒且无副作用", {
    "status": "failed", "code": "RISK_BLOCKED",
    "snapshot": dict(account=dict(balance_minor=200000, available_minor=200000, reserved_minor=0),
                     holdings=[], orders=[], ledger=[], challenges=0),
}, actual_mismatch)

# ---------------------------------------------------------------- G3-05 minimum / insufficient
_, minimum = purchase(r3_profile, "WP-C02", 9999)
_, insufficient = purchase(r3_profile, "WP-C02", 250000)
actual_limits = dict(
    minimum=dict(status=minimum.get("status"), code=code_of(minimum)),
    insufficient=dict(status=insufficient.get("status"), code=code_of(insufficient)),
    snapshot=snapshot(),
)
record("G3-05", "起购不足/余额不足均确认前拒绝", {
    "minimum": dict(status="failed", code="LIMIT_EXCEEDED"),
    "insufficient": dict(status="failed", code="INSUFFICIENT_FUNDS"),
    "snapshot": dict(account=dict(balance_minor=200000, available_minor=200000, reserved_minor=0),
                     holdings=[], orders=[], ledger=[], challenges=0),
}, actual_limits)

# ---------------------------------------------------------------- G3-06 purchase
request, pending = purchase(r3_profile, "WP-C02", 50000)
done = verify(pending) if pending.get("status") == "awaiting_verification" else pending
if done.get("status") == "succeeded":
    validate_result(request, done)
with store.connection() as c:
    holding_row = dict(c.execute("SELECT product_id,units,state,lock_days,confirmed_on FROM holdings ORDER BY rowid").fetchone())
actual_purchase = dict(
    pending_status=pending.get("status"),
    done_status=done.get("status"),
    amount_minor=(done.get("data") or {}).get("amount_minor"),
    snapshot=snapshot(),
    holding=holding_row,
)
record("G3-06", "正常申购 50000：红色强验证、费用/份额/余额", {
    "pending_status": "awaiting_verification",
    "done_status": "succeeded",
    "amount_minor": 50000,
    "snapshot": dict(
        account=dict(balance_minor=150000, available_minor=150000, reserved_minor=0),
        holdings=[dict(product_id="WP-C02", units="499.5000", state="open", lock_days=7, confirmed_on="2026-10-19")],
        orders=[dict(order_type="purchase", product_id="WP-C02", amount_minor=50000, fee_minor=50, units="499.5", status="succeeded")],
        ledger=[dict(direction="debit", amount_minor=50000, kind="wealth")],
        challenges=1,
    ),
    "holding": dict(product_id="WP-C02", units="499.5000", state="open", lock_days=7, confirmed_on="2026-10-19"),
}, actual_purchase)
holding_id = holding_row.get("holding_id") if "holding_id" in holding_row else None
# holding_id not selected above; fetch separately
with store.connection() as c:
    holding_id = c.execute("SELECT holding_id FROM holdings WHERE product_id='WP-C02'").fetchone()[0]

# ---------------------------------------------------------------- G3-07 context
context = gateway.get_context(principal_id=P)["holdings"]
record("G3-07", "持仓上下文只含 open 持仓", [
    dict(account_id=ACC, product_id="WP-C02", units="499.5000"),
], [{k: row[k] for k in ("account_id", "product_id", "units")} for row in context])

# ---------------------------------------------------------------- G3-08 locked redeem
_, early, early_done = redeem(holding_id, "100.0000")
actual_locked = dict(status=early_done.get("status"), code=code_of(early_done), snapshot=snapshot())
record("G3-08", "锁定期内赎回被拒且份额/余额不变", {
    "status": "failed", "code": "INVALID_STATE",
    "snapshot": dict(
        account=dict(balance_minor=150000, available_minor=150000, reserved_minor=0),
        holdings=[dict(product_id="WP-C02", units="499.5000", state="open", lock_days=7, confirmed_on="2026-10-19")],
        orders=[dict(order_type="purchase", product_id="WP-C02", amount_minor=50000, fee_minor=50, units="499.5", status="succeeded")],
        ledger=[dict(direction="debit", amount_minor=50000, kind="wealth")],
        challenges=1,
    ),
}, actual_locked)

# ---------------------------------------------------------------- G3-09 partial redeem at unlock day
now = now + timedelta(days=7)
_, pending, done = redeem(holding_id, "200.0000")
actual_partial = dict(
    pending_status=pending.get("status"), done_status=done.get("status"),
    units=(done.get("data") or {}).get("units"), snapshot=snapshot(),
    holding=snapshot()["holdings"][0],
)
record("G3-09", "解锁当日部分赎回 200 份", {
    "pending_status": "awaiting_verification",
    "done_status": "succeeded",
    "units": "200",
    "snapshot": dict(
        account=dict(balance_minor=170000, available_minor=170000, reserved_minor=0),
        holdings=[dict(product_id="WP-C02", units="299.5000", state="open", lock_days=7, confirmed_on="2026-10-19")],
        orders=[dict(order_type="purchase", product_id="WP-C02", amount_minor=50000, fee_minor=50, units="499.5", status="succeeded"),
                dict(order_type="redeem", product_id="WP-C02", amount_minor=20000, fee_minor=0, units="200", status="succeeded")],
        ledger=[dict(direction="debit", amount_minor=50000, kind="wealth"),
                dict(direction="credit", amount_minor=20000, kind="wealth")],
        challenges=2,
    ),
    "holding": dict(product_id="WP-C02", units="299.5000", state="open", lock_days=7, confirmed_on="2026-10-19"),
}, actual_partial)

# ---------------------------------------------------------------- G3-10 clear
_, pending, done = redeem(holding_id, "299.5000")
actual_clear = dict(
    pending_status=pending.get("status"), done_status=done.get("status"),
    units=(done.get("data") or {}).get("units"), snapshot=snapshot(),
    holding=snapshot()["holdings"][0],
    context=gateway.get_context(principal_id=P)["holdings"],
)
record("G3-10", "清仓：份额归零、持仓 closed、上下文移除", {
    "pending_status": "awaiting_verification",
    "done_status": "succeeded",
    "units": "299.5",
    "snapshot": dict(
        account=dict(balance_minor=199950, available_minor=199950, reserved_minor=0),
        holdings=[dict(product_id="WP-C02", units="0.0000", state="closed", lock_days=7, confirmed_on="2026-10-19")],
        orders=[dict(order_type="purchase", product_id="WP-C02", amount_minor=50000, fee_minor=50, units="499.5", status="succeeded"),
                dict(order_type="redeem", product_id="WP-C02", amount_minor=20000, fee_minor=0, units="200", status="succeeded"),
                dict(order_type="redeem", product_id="WP-C02", amount_minor=29950, fee_minor=0, units="299.5", status="succeeded")],
        ledger=[dict(direction="debit", amount_minor=50000, kind="wealth"),
                dict(direction="credit", amount_minor=20000, kind="wealth"),
                dict(direction="credit", amount_minor=29950, kind="wealth")],
        challenges=3,
    ),
    "holding": dict(product_id="WP-C02", units="0.0000", state="closed", lock_days=7, confirmed_on="2026-10-19"),
    "context": [],
}, actual_clear)

# ---------------------------------------------------------------- G3-11 redeem after clear
_, again, again_done = redeem(holding_id, "1.0000")
record("G3-11", "清仓后再赎回被拒", {"status": "failed", "code": "INVALID_STATE"},
       dict(status=again_done.get("status"), code=code_of(again_done)))

# ---------------------------------------------------------------- G3-12 fee rounding
_, pending = purchase(r3_profile, "WP-C02", 10500)
done = verify(pending) if pending.get("status") == "awaiting_verification" else pending
with store.connection() as c:
    fee_holding = dict(c.execute("SELECT units,state FROM holdings WHERE product_id='WP-C02' AND state='open' ORDER BY rowid DESC").fetchone())
    fee_order = dict(c.execute("SELECT fee_minor,units FROM wealth_orders WHERE order_type='purchase' ORDER BY rowid DESC").fetchone())
actual_fee = dict(
    pending_status=pending.get("status"), done_status=done.get("status"),
    fee_minor=fee_order.get("fee_minor"), order_units=fee_order.get("units"),
    holding_units=fee_holding.get("units"),
    balance=snapshot()["account"]["balance_minor"],
)
record("G3-12", "0.5 分费用进位：10500 申购费 11、份额 104.8900", {
    "pending_status": "awaiting_verification", "done_status": "succeeded",
    "fee_minor": 11, "order_units": "104.89", "holding_units": "104.8900", "balance": 189450,
}, actual_fee)

# ---------------------------------------------------------------- G3-13 entry matrix
matrix_file = PKG / "03_入口矩阵_理财四类.json"
matrix = json.loads(matrix_file.read_text(encoding="utf-8"))
actual_matrix = []
for item in matrix["cases"]:
    result = route(item["text"])
    expected = item["expected"]
    actual = dict(status=result.get("status"), intent=result.get("intent"),
                  fields=result.get("fields", {}), missing=sorted(result.get("missing", [])))
    exp = dict(status=expected["status"], intent=expected["intent"],
               fields=expected.get("fields", {}), missing=sorted(expected.get("missing", [])))
    records.append(dict(id=item["id"], title=item["text"], expected=exp, actual=actual,
                        match=(exp == actual), notes="route() 纯函数"))
    if exp != actual:
        fail(item["id"], f"route mismatch: {exp} vs {actual}")

# ---------------------------------------------------------------- G01-G15 table
gfile = PKG / "02_G01-G15覆盖表.md"
gtext = gfile.read_text(encoding="utf-8") if gfile.exists() else ""
g_rows = re.findall(r"^\|\s*(G\d{2})\s*\|", gtext, re.MULTILINE)
g_missing = [f"G{i:02d}" for i in range(1, 16) if f"G{i:02d}" not in set(g_rows)]
if len(g_rows) != 15 or g_missing:
    fail("G01-G15", f"覆盖表行数 {len(g_rows)}，缺 {g_missing}")

# ---------------------------------------------------------------- summary
summary = dict(
    package="C 第五轮组 3 理财联合验收",
    member="C",
    round="第五轮（2026-10-20 分派）",
    version="C5-1.0",
    baseline="main@a505d20",
    generated_at=datetime.now(SHANGHAI).isoformat(),
    python=sys.version,
    db=str(db_path),
    group3=[r for r in records if r["id"].startswith("G3-")],
    entry=[r for r in records if r["id"].startswith("C5-ENT")],
    g_table=dict(rows=len(g_rows), missing=g_missing),
    failure_count=len(failures),
    failures=failures,
    result="PASS" if not failures else "FAIL",
    limits=[
        "本机实跑 13 项组 3/费用/入口检查；页面（Streamlit）未重跑。",
        "使用临时 SQLite（非 runtime/ 日常库）；金额为整数分，份额为契约十进制字符串。",
        "G01—G07、G09—G12、G14—G15 的 A 自动化证据仅引用测试名，不由 C 冒充独立执行；G13 未执行。",
        "R01/R03-B 按 C-07 继续不启用，本包不涉及。",
    ],
)
# write both selfcheck and group3 record
(PKG / "selfcheck.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
record_doc = dict(
    package="C 第五轮组 3 执行记录",
    member="C", round="第五轮（2026-10-20 分派）", version="C5-1.0",
    baseline="main@a505d20", generated_at=summary["generated_at"],
    environment=dict(python=sys.version, db=str(db_path), executor="C"),
    cases=summary["group3"],
)
(PKG / "01_组3执行记录.json").write_text(json.dumps(record_doc, ensure_ascii=False, indent=2), encoding="utf-8")

# cleanup temp db if possible
for path in (db_path, db_path.with_name(db_path.name + "-journal"), db_path.with_name(db_path.name + "-wal")):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
if not os.environ.get("C5_TMP"):
    shutil.rmtree(base_dir, ignore_errors=True)

print(json.dumps(summary, ensure_ascii=False, indent=2))
raise SystemExit(0 if summary["result"] == "PASS" else 1)
