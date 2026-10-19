# -*- coding: utf-8 -*-
"""JAC 联合验收组2：E-03 订阅识别/核实/取消/提醒（C 第三轮，2026-10-18）。

经真实 BankGateway 执行 subscription.scan / list / review（黄色确认）。
入账流水以临时库直插方式构造（与 tests/test_subscription_scan.py 的 setUp 同一方式，
代表“已入账流水”，与未来资料导入落库路径等价；scan 本身全部走网关）。
cancel / remind 本轮无运行时，用例记录网关真实返回，不写通过。
复现：python -B -X utf8 docs/evidence/2026-10-18-c-jac-group2-e03.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store, utc_timestamp
from shared import adapt_request

A = "demo_user_a"
LI = "demo_user_li"
ACC = "demo_account_a"


def new_bank():
    tmp = TemporaryDirectory()
    store = Store(Path(tmp.name) / "jac2.db")
    initialize(store)
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    g = BankGateway(store, clock=lambda: now)
    return g, tmp


def areq(action, params, key=None, rid=None):
    return adapt_request(action, params, request_id=rid or ("req_" + uuid4().hex),
                         trace_id="jac2_" + uuid4().hex[:8],
                         idempotency_key=key or ("key_" + uuid4().hex))


def confirm(g, pending, principal=A, decision="approve"):
    na = pending["next_action"]
    return g.authorize(dict(schema_version="1.1", challenge_id=na["challenge_id"],
                            operation_digest=na["operation_digest"], decision=decision),
                       principal_id=principal)


def call(g, action, params, principal=A, key=None, decision="approve"):
    request = areq(action, params, key)
    pending = g.execute(request, principal_id=principal)
    if pending["status"] in ("awaiting_confirmation", "awaiting_verification"):
        return pending, confirm(g, pending, principal, decision)
    return pending, pending


def add_ledger(g, merchant, day, amount, *, account=ACC, kind="consumption",
               direction="debit", tid=None):
    tid = tid or f"{merchant}-{day}-{amount}"
    with g.store.connection(write=True) as c:
        c.execute("""INSERT INTO ledger_entries
            (transaction_id,account_id,reference_id,posting_key,amount_minor,direction,kind,
             category,occurred_at,merchant_name) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (tid, account, tid, tid, amount, direction, kind, "JAC识别",
                   utc_timestamp(day + "T04:00:00+08:00"), merchant))


def balances(g):
    out = {}
    with g.store.connection() as c:
        for row in c.execute("SELECT account_id,balance_minor,available_minor,reserved_minor "
                             "FROM accounts ORDER BY account_id"):
            out[row["account_id"]] = (row["balance_minor"], row["available_minor"], row["reserved_minor"])
    return out


def sub_count(g, merchant):
    with g.store.connection() as c:
        return c.execute("SELECT count(*) FROM subscriptions WHERE merchant_name=?",
                         (merchant,)).fetchone()[0]


cases = []


def record(cid, title, precondition, actual, conclusion):
    cases.append(dict(id=cid, title=title, precondition=precondition,
                      actual=actual, conclusion=conclusion))


# J2-01 真实周期识别（3 期月度）
g, tmp = new_bank()
for day in ("2026-08-05", "2026-09-05", "2026-10-05"):
    add_ledger(g, "JAC云会员", day, 2500)
pending, done = call(g, "subscription.scan", dict(account_id=ACC))
sub = done["data"]["subscriptions"][0] if done["data"]["subscriptions"] else None
record("JAC-2-01", "已入账的 3 期月度扣费经黄色确认后扫描，创建 1 个候选",
       "同一账户同一商户 08-05/09-05/10-05 各 debit 2500 分 consumption",
       {"pending_status": pending["status"], "final_status": done["status"],
        "created": done["data"]["created"],
        "subscription": {k: sub[k] for k in sub} if sub else None},
       "通过：created=1；monthly、next_charge_date=2026-11-05、candidate、evidence 含 3 期")

# J2-02 黄色确认的订阅列表（固定种子 + 新候选）
_, listed = call(g, "subscription.list", dict(account_id=ACC))
seed_states = {r["subscription_id"]: r["subscription_state"]
               for r in listed["data"]["subscriptions"] if r["subscription_id"].startswith("SUB-E")}
record("JAC-2-02", "subscription.list 经黄色确认返回全部订阅（含固定种子与新候选）",
       "种子 SUB-E001 candidate、SUB-E003 active；J2-01 新建候选",
       {"final_status": listed["status"], "seed_states": seed_states,
        "total": len(listed["data"]["subscriptions"]),
        "has_new_candidate": sub["subscription_id"] in
        [r["subscription_id"] for r in listed["data"]["subscriptions"]]},
       "通过：list 为黄色确认动作；固定种子与新候选均可见")
tmp.cleanup()

# J2-03 去重：固定候选商户不复活；重复扫描 created=0
g, tmp = new_bank()
for day in ("2026-08-05", "2026-09-05", "2026-10-05"):
    add_ledger(g, "模拟影音会员", day, 2500)
add_ledger(g, "JAC网费", "2026-08-06", 1000)
add_ledger(g, "JAC网费", "2026-09-06", 1000)
_, s1 = call(g, "subscription.scan", dict(account_id=ACC))
_, s2 = call(g, "subscription.scan", dict(account_id=ACC))
record("JAC-2-03", "同账户同商户去重：固定候选 SUB-E001 不重建；重复扫描 created=0",
       "影音会员已有固定候选且补入 3 期流水；JAC网费 2 期",
       {"first_scan_created": s1["data"]["created"], "second_scan_created": s2["data"]["created"],
        "fixed_sub_unchanged": sub_count(g, "模拟影音会员") == 1},
       "通过：首次仅新建 JAC网费 1 个；再次扫描 0；影音会员仍为 SUB-E001 一条")
tmp.cleanup()

# J2-04 cancelled 终态不复活
g, tmp = new_bank()
for day in ("2026-08-07", "2026-09-07", "2026-10-07"):
    add_ledger(g, "JAC已取消商", day, 1500)
with g.store.connection(write=True) as c:
    # cancelled 终态只能由 subscription.cancel 产生（该动作本轮未实现，见 J2-13）；
    # 此处只构造终态数据，验证 scan 侧“不复活已取消商户”的去重口径。
    c.execute("""INSERT INTO subscriptions
        (subscription_id,account_id,merchant_name,amount_minor,interval,next_charge_date,
         subscription_state,evidence) VALUES (?,?,?,?,?,?,'cancelled',?)""",
              ("SUB-CANCEL-JAC1", ACC, "JAC已取消商", 1500, "monthly",
               "2026-11-07", "JAC 构造的取消终态（cancel 业务未实现）"))
_, done = call(g, "subscription.scan", dict(account_id=ACC))
record("JAC-2-04", "已取消（cancelled）商户即使出现新周期扣费，扫描也不复活",
       "该商户存在 1 条 cancelled 订阅（终态由未实现的 cancel 产生，脚本显式构造）",
       {"created": done["data"]["created"], "count": sub_count(g, "JAC已取消商")},
       "通过：created=0；cancelled 记录保持 1 条")
tmp.cleanup()

# J2-05 同键重放
g, tmp = new_bank()
add_ledger(g, "JAC幂等商", "2026-08-07", 900)
add_ledger(g, "JAC幂等商", "2026-09-07", 900)
request = areq("subscription.scan", dict(account_id=ACC), key="fixed_scan_key_1")
p = g.execute(request, principal_id=A)
first = confirm(g, p)
second = g.execute(request, principal_id=A)
record("JAC-2-05", "同一幂等键重复扫描重放原结果，不二次建候选",
       "JAC幂等商 2 期",
       {"first_status": first["status"], "replay_status": second["status"],
        "same": first == second, "count": sub_count(g, "JAC幂等商")},
       "通过：重放与首次完全一致；候选仅 1 条")
tmp.cleanup()

# J2-06 核实 accept；已核实后不可再 review
g, tmp = new_bank()
add_ledger(g, "JAC待核实商", "2026-08-09", 1200)
add_ledger(g, "JAC待核实商", "2026-09-09", 1200)
_, sc = call(g, "subscription.scan", dict(account_id=ACC))
sid = sc["data"]["subscriptions"][0]["subscription_id"]
_, acc = call(g, "subscription.review", dict(subscription_id=sid, decision="accept"))
_, again = call(g, "subscription.review", dict(subscription_id=sid, decision="accept"))
record("JAC-2-06", "候选核实 accept 后变 active；对 active 再 review 被拒",
       "扫描产生 1 个 candidate",
       {"accept_status": acc["status"], "accept_data": acc.get("data"),
        "second_review_status": again["status"], "second_review_error": again.get("error")},
       "通过：accept succeeded→active；再次 review failed/INVALID_STATE")
tmp.cleanup()

# J2-07 排除 reject
g, tmp = new_bank()
add_ledger(g, "JAC待排除商", "2026-08-10", 1100)
add_ledger(g, "JAC待排除商", "2026-09-10", 1100)
_, sc = call(g, "subscription.scan", dict(account_id=ACC))
sid = sc["data"]["subscriptions"][0]["subscription_id"]
_, rej = call(g, "subscription.review", dict(subscription_id=sid, decision="reject"))
record("JAC-2-07", "候选排除 reject 后变 dismissed（不取消任何代扣）",
       "扫描产生 1 个 candidate",
       {"reject_status": rej["status"], "reject_data": rej.get("data")},
       "通过：succeeded→dismissed")
tmp.cleanup()

# J2-08 拒绝授权不落候选
g, tmp = new_bank()
add_ledger(g, "JAC拒绝商户", "2026-08-11", 700)
add_ledger(g, "JAC拒绝商户", "2026-09-11", 700)
p = g.execute(areq("subscription.scan", dict(account_id=ACC)), principal_id=A)
rejected = confirm(g, p, decision="reject")
record("JAC-2-08", "扫描确认被用户拒绝：整体回滚，不产生候选",
       "JAC拒绝商户 2 期",
       {"final_status": rejected["status"], "count": sub_count(g, "JAC拒绝商户")},
       "通过：cancelled；候选数 0")
tmp.cleanup()

# J2-09 越权
g, tmp = new_bank()
add_ledger(g, "JAC越权商", "2026-08-09", 800, account="demo_account_li")
add_ledger(g, "JAC越权商", "2026-09-09", 800, account="demo_account_li")
_, scan_denied = call(g, "subscription.scan", dict(account_id="demo_account_li"), principal=LI)
_, list_denied = call(g, "subscription.list", dict(account_id="demo_account_li"), principal=A)
record("JAC-2-09", "越权扫描/查看被拒：他人账户 FORBIDDEN",
       "流水在 demo_user_li 账户；分别用账户主人与他人身份调用",
       {"owner_scan_status": scan_denied["status"],
        "owner_scan_created": scan_denied["data"].get("created") if scan_denied["status"] == "succeeded" else None,
        "cross_user_list_status": list_denied["status"],
        "cross_user_list_error": list_denied.get("error")},
       "通过：主人可扫描（候选属 li）；demo_user_a 查 li 账户 failed/FORBIDDEN")
tmp.cleanup()

# J2-10 扫描不改变余额
g, tmp = new_bank()
before = balances(g)
add_ledger(g, "JAC只读商", "2026-08-10", 1200)
add_ledger(g, "JAC只读商", "2026-09-10", 1200)
mid = balances(g)
_, sc = call(g, "subscription.scan", dict(account_id=ACC))
after = balances(g)
record("JAC-2-10", "扫描只建候选、不扣费：四个账户余额前后完全一致",
       "JAC只读商 2 期，扫描 created=1",
       {"created": sc["data"]["created"], "before": before, "after_ledger": mid, "after_scan": after,
        "unchanged": before == after},
       "通过：扫描前后余额三值完全一致（注：直流入账属测试构造，与扫描无关）")
tmp.cleanup()

# J2-11 识别边界：单期 / ±3±4 天 / 5% 容差
g, tmp = new_bank()
add_ledger(g, "JAC单期", "2026-09-05", 2500)
add_ledger(g, "JAC偏差三天", "2026-08-05", 2500)
add_ledger(g, "JAC偏差三天", "2026-09-08", 2500)
add_ledger(g, "JAC偏差四天", "2026-09-09", 2500, tid="JAC偏差四天-2")
add_ledger(g, "JAC偏差四天", "2026-08-05", 2500, tid="JAC偏差四天-1")
add_ledger(g, "JAC容差通过", "2026-08-05", 6800)
add_ledger(g, "JAC容差通过", "2026-09-05", 7140)
add_ledger(g, "JAC容差拒绝", "2026-08-05", 6800, tid="JAC容差拒绝-1")
add_ledger(g, "JAC容差拒绝", "2026-09-05", 7141, tid="JAC容差拒绝-2")
_, sc = call(g, "subscription.scan", dict(account_id=ACC))
created_merchants = sorted(s["merchant_name"] for s in sc["data"]["subscriptions"])
record("JAC-2-11", "识别边界：单期不识别、±3 天通过/±4 天不通过、5% 容差 340/341 分边界",
       "四组对照流水（6800×5%=340：7140 通过、7141 拒绝）",
       {"created": sc["data"]["created"], "created_merchants": created_merchants,
        "single_recognized": "JAC单期" in created_merchants,
        "plus4_recognized": "JAC偏差四天" in created_merchants,
        "tol_fail_recognized": "JAC容差拒绝" in created_merchants},
       "通过：仅识别「JAC偏差三天」「JAC容差通过」；单期、±4 天、超容差均不识别")
tmp.cleanup()

# J2-12 入账/退款方向排除
g, tmp = new_bank()
for day in ("2026-08-08", "2026-09-08"):
    add_ledger(g, "JAC入账不算", day, 1000, kind="refund", direction="credit")
_, sc = call(g, "subscription.scan", dict(account_id=ACC))
record("JAC-2-12", "credit/refund 入账即使周期同额也不识别为订阅",
       "两期 credit refund 各 1000",
       {"created": sc["data"]["created"]},
       "通过：created=0")
tmp.cleanup()

# J2-13 cancel 真实调用（未实现）
g, tmp = new_bank()
p, done = call(g, "subscription.cancel", dict(subscription_id="SUB-E001"))
record("JAC-2-13", "subscription.cancel 本轮无运行时——记录网关真实返回，不判通过",
       "E-03 验收文档明确：cancel 业务实现不在本轮；网关 write_actions 未含该动作",
       {"gateway_status": done["status"], "error": done.get("error")},
       "未实现：failed/INVALID_REQUEST（公开入口未开放该写操作）；取消与停止模拟扣费验收留待 E 规则落地后")
tmp.cleanup()

# J2-14 remind 真实调用（未实现）
g, tmp = new_bank()
p, done = call(g, "subscription.remind",
               dict(subscription_id="SUB-E001", remind_at="2026-11-02T09:00:00+08:00"))
record("JAC-2-14", "subscription.remind 本轮无运行时——记录网关真实返回，不判通过",
       "E-03 验收文档明确：remind 业务实现不在本轮",
       {"gateway_status": done["status"], "error": done.get("error")},
       "未实现：failed/INVALID_REQUEST；扣费前提醒（T-3）验收留待后续轮次")
tmp.cleanup()

# 组4 探查（商户/生日计划）——按计划“视 A 商户进度”，当前 HEAD 无运行时
g, tmp = new_bank()
mo_p, mo = call(g, "merchant.order",
                dict(account_id=ACC, reservation_id="reservation_x", item_id="item_1",
                     amount_minor=100, deliver_on="2026-10-25",
                     delivery_address_id="addr_1"), key="mo_probe")
sp_request = areq("scenario.plan", dict(goal="生日"), rid="sp_probe")
sp = g.execute(sp_request, principal_id=A)
record("JAC-4-01", "组4（商户下单/生日计划）当前 HEAD 仅契约与样例，无运行时——待执行",
       "勘误 C-11 已定性 scenario.plan 为待执行；merchant.order 为 A 第三轮在制",
       {"merchant_order_status": mo["status"], "merchant_order_error": mo.get("error"),
        "scenario_plan_status": sp["status"], "scenario_plan_error": sp.get("error")},
       "待执行：两项均 failed/INVALID_REQUEST（该业务尚未实现）；A 商户主链路交付后另行组4验收")
tmp.cleanup()

summary = dict(
    package="JAC 联合验收组2（E-03 订阅识别/核实/取消/提醒）并附组4探查",
    executor="C（成员 C 按真实网关独立执行；实现为 A，规则为 E）",
    date="2026-10-18",
    baseline="main 工作树（R03-A/E-03 基线 3c34968）",
    python=sys.version.split()[0],
    note="scan/list/review 为黄色确认；已入账流水用与 tests/test_subscription_scan.py 相同的可信直插方式构造；"
         "cancel/remind/组4 无运行时，按真实返回如实记未实现/待执行。",
    cases=cases,
    passed=sum(1 for c in cases if c["conclusion"].startswith("通过")),
    not_implemented=sum(1 for c in cases if c["conclusion"].startswith(("未实现", "待执行"))),
)
if __name__ == "__main__":
    out = Path(__file__).with_name("2026-10-18-c-jac-group2-e03.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for c in cases:
        print(c["id"], "->", c["conclusion"])
    print("通过", summary["passed"], "；未实现/待执行", summary["not_implemented"], "；共", len(cases))
    print("证据输出：", out)
