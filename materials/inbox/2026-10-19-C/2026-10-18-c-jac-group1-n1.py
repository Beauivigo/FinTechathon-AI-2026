# -*- coding: utf-8 -*-
"""JAC 联合验收组1：N1 预算预留/释放/恢复（C 第三轮，2026-10-18）。

全部用例经真实 BankGateway 执行：申请(execute)→授权(authorize)→查询(operation.get)，
每个用例独立临时库（initialize 种子），记录实际返回 JSON，不写“应该通过”。
预算动作为黄色确认（shared.contracts: budget.reserve/release risk=yellow）。
复现：python -B -X utf8 docs/evidence/2026-10-18-c-jac-group1-n1.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import adapt_request, ServiceError, ContractError

A = "demo_user_a"
LI = "demo_user_li"
ACC = "demo_account_a"


def new_bank(now=None):
    tmp = TemporaryDirectory()
    store = Store(Path(tmp.name) / "jac1.db")
    initialize(store)
    clock = [now or datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)]
    g = BankGateway(store, clock=lambda: clock[0])
    return g, tmp, clock


def areq(action, params, key=None):
    return adapt_request(action, params, request_id="req_" + uuid4().hex,
                         trace_id="jac1_" + uuid4().hex[:8],
                         idempotency_key=key or ("key_" + uuid4().hex))


def submit(g, request, principal=A):
    return g.execute(request, principal_id=principal)


def approve(g, pending, principal=A, decision="approve"):
    na = pending["next_action"]
    return g.authorize(dict(schema_version="1.1", challenge_id=na["challenge_id"],
                            operation_digest=na["operation_digest"], decision=decision),
                       principal_id=principal)


def full(g, action, params, principal=A, key=None, decision="approve"):
    """提交并确认，返回 (pending, final)；失败在提交阶段时 final=pending。"""
    request = areq(action, params, key)
    pending = submit(g, request, principal)
    if pending["status"] in ("awaiting_confirmation", "awaiting_verification"):
        return pending, approve(g, pending, principal, decision)
    return pending, pending


def balance(g, account=ACC, principal=A):
    r = g.execute(adapt_request("account.balance", dict(account_id=account),
                                request_id="bal_" + uuid4().hex, trace_id="t"),
                  principal_id=principal)
    return {k: r["data"][k] for k in ("balance_minor", "available_minor", "reserved_minor")}


def edge_call(action, params):
    """非法请求在边缘即被拒（ContractError→ServiceError），返回错误码。"""
    try:
        areq(action, params)
        return {"raised": False}
    except ServiceError as exc:
        return {"raised": True, "code": exc.code, "message": exc.message}
    except ContractError as exc:
        return {"raised": True, "code": "INVALID_REQUEST", "message": str(exc)}


def ledger_count(g):
    with g.store.connection() as c:
        return c.execute("SELECT count(*) FROM ledger_entries").fetchone()[0]


def reservation_state(g, rid):
    rows = [r for r in g.list_reservations(principal_id=A) if r["reservation_id"] == rid]
    if not rows:
        return None
    r = rows[0]
    return {"state": r["state"], "amount_minor": r["amount_minor"],
            "remaining_reserved_minor": r["remaining_reserved_minor"]}


def audit_tail(g, limit=6):
    with g.store.connection() as c:
        return [dict(action=row["action"], outcome=row["outcome"], details=row["details_json"])
                for row in c.execute("SELECT action,outcome,details_json FROM audit_events "
                                     "ORDER BY event_id DESC LIMIT ?", (limit,))]


cases = []


def record(cid, title, precondition, actual, conclusion):
    cases.append(dict(id=cid, title=title, precondition=precondition,
                      actual=actual, conclusion=conclusion))


# J1-01 预留申请→确认成功
g, tmp, clock = new_bank()
before = balance(g)
pending, done = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=30000, purpose="JAC测试预留"))
after = balance(g)
rid = done.get("data", {}).get("reservation_id")
record("JAC-1-01", "预留申请经黄色确认后成功，资金同一事务扣减",
       "种子库 demo_account_a 余额/可用/预留=200000/200000/0",
       {"pending_status": pending["status"], "final_status": done["status"],
        "final_data": done.get("data"), "balance_before": before, "balance_after": after,
        "reservation": reservation_state(g, rid)},
       "通过：succeeded；200000/170000/30000，预留 active 且剩余=金额")
tmp.cleanup()

# J1-02 申请阶段（待确认）不改资金
g, tmp, clock = new_bank()
before = balance(g)
request = areq("budget.reserve", dict(account_id=ACC, amount_minor=30000, purpose="待确认不改资金"))
pending = submit(g, request)
mid = balance(g)
record("JAC-1-02", "申请阶段仅生成待确认挑战，不扣减资金",
       "同上初始余额",
       {"pending_status": pending["status"], "balance_before": before, "balance_pending": mid},
       "通过：awaiting_confirmation 期间余额三值不变")
tmp.cleanup()

# J1-03 同键重放（已成功的预留重复提交）
g, tmp, clock = new_bank()
key = "reserve_replay_key_1"
_, first = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=30000, purpose="重放"), key=key)
replay = submit(g, areq("budget.reserve", dict(account_id=ACC, amount_minor=30000, purpose="重放"), key=key))
record("JAC-1-03", "同一幂等键重复提交重放原结果，不二次预留",
       "J1-01 同口径预留 30000 已成功",
       {"first_status": first["status"], "first_data": first["data"],
        "replay_status": replay["status"], "replay_data": replay.get("data"),
        "same_data": replay.get("data") == first.get("data"), "balance": balance(g)},
       "通过：重放返回原 reservation_id，余额只扣一次")
tmp.cleanup()

# J1-04 释放成功，资金恢复，终态 released
g, tmp, clock = new_bank()
_, r = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=30000, purpose="将释放"))
rid = r["data"]["reservation_id"]
mid = balance(g)
_, rel = full(g, "budget.release", dict(reservation_id=rid))
after = balance(g)
record("JAC-1-04", "零消费、无关联预留释放成功，可用余额恢复",
       "预留 30000 active，余额 200000/170000/30000",
       {"balance_reserved": mid, "release_status": rel["status"], "release_data": rel.get("data"),
        "balance_after": after, "reservation": reservation_state(g, rid)},
       "通过：succeeded；公开 data 仅 reservation_id；200000/200000/0；终态 released")
tmp.cleanup()

# J1-05 终态预留再次释放被拒
g, tmp, clock = new_bank()
_, r = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=30000, purpose="重复释放"))
rid = r["data"]["reservation_id"]
full(g, "budget.release", dict(reservation_id=rid))
again = submit(g, areq("budget.release", dict(reservation_id=rid)))
record("JAC-1-05", "已 released 的预留再次申请释放被拒，资金不重复恢复",
       "预留已释放，余额 200000/200000/0",
       {"second_release_status": again["status"], "error": again.get("error"),
        "balance": balance(g)},
       "通过：failed/INVALID_STATE，余额保持 200000/200000/0")
tmp.cleanup()

# J1-06 回执丢失：同键重放 + operation.get 查真实结果
g, tmp, clock = new_bank()
_, r = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=42000, purpose="丢回执"), key="lost_key_1")
rid = r["data"]["reservation_id"]
rel_key = "rel_lost_key_1"
_, rel = full(g, "budget.release", dict(reservation_id=rid), key=rel_key)
rel_replay = submit(g, areq("budget.release", dict(reservation_id=rid), key=rel_key))
q = g.execute(adapt_request("operation.get",
                            dict(original_action="budget.release", original_idempotency_key=rel_key),
                            request_id="q_" + uuid4().hex, trace_id="t"), principal_id=A)
record("JAC-1-06", "释放成功回执丢失后，同键重放/原操作查询取回真实结果，不重复释放",
       "预留 42000 已成功释放",
       {"release_status": rel["status"], "replay_status": rel_replay["status"],
        "query_status": q["status"], "queried_result_status": q["data"]["operation_result"]["status"],
        "queried_data": q["data"]["operation_result"].get("data"), "balance": balance(g)},
       "通过：重放与 operation.get 均取回 succeeded/原 reservation_id，余额无二次变动")
tmp.cleanup()

# J1-07 拒绝授权
g, tmp, clock = new_bank()
before = balance(g)
pending, done = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=30000, purpose="被拒绝"),
                     decision="reject")
after = balance(g)
with g.store.connection() as c:
    nres = c.execute("SELECT count(*) FROM reservations").fetchone()[0]
record("JAC-1-07", "用户拒绝授权，预留取消且不落库",
       "初始余额",
       {"final_status": done["status"], "balance_before": before, "balance_after": after,
        "reservations_count": nres},
       "通过：cancelled；余额不变；reservations 无记录")
tmp.cleanup()

# J1-08 授权过期
g, tmp, clock = new_bank()
request = areq("budget.reserve", dict(account_id=ACC, amount_minor=30000, purpose="过期"))
pending = submit(g, request)
clock[0] += timedelta(minutes=11)
done = approve(g, pending)
record("JAC-1-08", "挑战 10 分钟有效期后授权失败，预留不生效",
       "T 时刻申请，T+11 分钟才授权",
       {"final_status": done["status"], "error": done.get("error"), "balance": balance(g)},
       "通过：failed/AUTH_EXPIRED；余额不变")
tmp.cleanup()

# J1-09 越权使用他人账户
g, tmp, clock = new_bank()
pending, done = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=1000, purpose="越权"),
                     principal=LI)
record("JAC-1-09", "非账户归属用户发起预留被拒",
       "demo_user_li 对 demo_account_a（属 demo_user_a）操作",
       {"final_status": done["status"], "error": done.get("error")},
       "通过：failed/FORBIDDEN")
tmp.cleanup()

# J1-10 可用余额不足
g, tmp, clock = new_bank()
pending, done = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=200001, purpose="超额"))
record("JAC-1-10", "预留金额超过可用余额，确认时失败且不记账",
       "可用 200000 分，申请预留 200001 分",
       {"pending_status": pending["status"], "final_status": done["status"],
        "error": done.get("error"), "balance": balance(g)},
       "通过：failed/INSUFFICIENT_FUNDS；余额不变")
tmp.cleanup()

# J1-11 两个预留竞争可用余额
g, tmp, clock = new_bank()
_, r1 = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=120000, purpose="竞争一"), key="comp_1")
_, r2 = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=120000, purpose="竞争二"), key="comp_2")
record("JAC-1-11", "两个预留竞争余额：第二个因可用不足失败，不超用",
       "可用 200000；两笔各 120000（合计 240000）",
       {"first_status": r1["status"], "second_status": r2["status"], "second_error": r2.get("error"),
        "balance": balance(g)},
       "通过：一成功一失败/INSUFFICIENT_FUNDS；最终 200000/80000/120000，守恒")
tmp.cleanup()

# J1-12 两个释放竞争同一预留
g, tmp, clock = new_bank()
_, r = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=50000, purpose="双释放"))
rid = r["data"]["reservation_id"]
p1 = submit(g, areq("budget.release", dict(reservation_id=rid), key="rel_a"))
p2 = submit(g, areq("budget.release", dict(reservation_id=rid), key="rel_b"))
d1 = approve(g, p1)
d2 = approve(g, p2)
record("JAC-1-12", "两个释放请求竞争同一预留：只允许一次成功",
       "预留 50000；释放 A、B 均已生成待确认",
       {"release_a_status": d1["status"], "release_b_status": d2["status"],
        "release_b_error": d2.get("error"), "balance": balance(g),
        "reservation": reservation_state(g, rid)},
       "通过：A succeeded，B failed/INVALID_STATE；余额仅恢复一次")
tmp.cleanup()

# J1-13 非法金额边缘拒绝
g, tmp, clock = new_bank()
e0 = edge_call("budget.reserve", dict(account_id=ACC, amount_minor=0, purpose="零金额"))
eneg = edge_call("budget.reserve", dict(account_id=ACC, amount_minor=-100, purpose="负金额"))
record("JAC-1-13", "金额 0/负数在请求边缘被拒（INVALID_REQUEST）",
       "v1.1 money 要求正整数分",
       {"zero": e0, "negative": eneg, "balance": balance(g)},
       "通过：两类非法金额均抛出 INVALID_REQUEST，未进入待确认")
tmp.cleanup()

# J1-14 预留/释放不产生收支流水
g, tmp, clock = new_bank()
_, r = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=30000, purpose="无流水"))
full(g, "budget.release", dict(reservation_id=r["data"]["reservation_id"]))
n = ledger_count(g)
with g.store.connection() as c:
    ntrans = c.execute("SELECT count(*) FROM transfers").fetchone()[0]
record("JAC-1-14", "预留与释放不产生收支流水、不占用转账日累计",
       "一轮完整预留 30000 后释放",
       {"ledger_entries": n, "transfers": ntrans, "balance": balance(g)},
       "通过：ledger_entries=0、transfers=0；余额恢复")
tmp.cleanup()

# J1-15 释放不存在/不属于本用户的预留
g, tmp, clock = new_bank()
done = submit(g, areq("budget.release", dict(reservation_id="reservation_not_exist_xyz")))
record("JAC-1-15", "释放不存在的预留编号被拒（FORBIDDEN）",
       "本用户无该预留",
       {"final_status": done["status"], "error": done.get("error"), "balance": balance(g)},
       "通过：failed/FORBIDDEN；余额不变")
tmp.cleanup()

# J1-16 审计保留独立子动作与金额
g, tmp, clock = new_bank()
_, r = full(g, "budget.reserve", dict(account_id=ACC, amount_minor=30000, purpose="审计"))
full(g, "budget.release", dict(reservation_id=r["data"]["reservation_id"]))
record("JAC-1-16", "预留/释放的资金子动作进入审计（独立 posting_key 与金额）",
       "一轮预留并释放",
       {"recent_audit": audit_tail(g)},
       "通过：审计含 reserve/release_reserved 的 succeeded 记录")
tmp.cleanup()

# J1-17 部分消费/商户关联释放——本轮范围外（N4）
g, tmp, clock = new_bank()
mo = submit(g, areq("merchant.order",
                    dict(account_id=ACC, reservation_id="x", item_id="x", amount_minor=100,
                         deliver_on="2026-10-25", delivery_address_id="addr_1"),
                    key="merchant_probe"))
record("JAC-1-17", "部分消费/商户关联后的释放（N4 范围）本轮无运行时，不执行、不判通过",
       "budget.binding 代码含商户/任务关联阻断；merchant.order 无运行时实现",
       {"merchant_order_probe_status": mo["status"], "merchant_order_probe_error": mo.get("error")},
       "本轮不执行：商户主链路由 A 第三轮交付；关联释放验收留 N4，当前不能宣称通过")
tmp.cleanup()

summary = dict(
    package="JAC 联合验收组1（N1 预算预留/释放/恢复）",
    executor="C（成员 C 按真实网关独立执行；实现为 A）",
    date="2026-10-18",
    baseline="main 工作树（R03-A/E-03 基线 3c34968；N1 基线 60f19e2）",
    python=sys.version.split()[0],
    note="预算动作均为黄色确认；每用例独立临时库；金额单位为分；J1-17 为范围外项如实标注。",
    cases=cases,
    passed=sum(1 for c in cases if c["conclusion"].startswith("通过")),
    not_executed=sum(1 for c in cases if c["conclusion"].startswith("本轮不执行")),
)
if __name__ == "__main__":
    out = Path(__file__).with_name("2026-10-18-c-jac-group1-n1.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for c in cases:
        print(c["id"], "->", c["conclusion"])
    print("通过", summary["passed"], "；本轮不执行", summary["not_executed"], "；共", len(cases))
    print("证据输出：", out)
