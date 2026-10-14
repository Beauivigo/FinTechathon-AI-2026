# -*- coding: utf-8 -*-
"""B 侧独立资金/权限实测：每个场景独立临时库，记录余额、状态、错误码。
仅用于成员独立验收，全部为本地虚构余额与模拟验证（demo_otp）。
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import adapt_request, ServiceError
from shared.services import validate_funds_command  # noqa: F401

UTC = timezone.utc
SH = timezone(timedelta(hours=8))
A = "demo_user_a"
LI_USER = "demo_user_li"
ACC = "demo_account_a"
LI_PAYEE = "demo_payee_li"
W1 = "demo_payee_wang_1"
W2 = "demo_payee_wang_2"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def new_bank(t=None):
    tmp = TemporaryDirectory(prefix="b-accept-")
    store = Store(Path(tmp.name) / "bank.sqlite3")
    initialize(store)
    clock = Clock(t or datetime(2026, 10, 15, 6, 0, 0, tzinfo=UTC))
    gw = BankGateway(store, clock=clock)
    return tmp, store, gw, clock


def balance(gw, acc=ACC, principal=None):
    if principal is None:
        principal = LI_USER if acc == "demo_account_li" else A
    r = gw.execute(adapt_request("account.balance", {"account_id": acc},
                                 request_id="rb", trace_id="tb"), principal_id=principal)
    return r["data"]["balance_minor"]


def yuan(c):
    return f"{c // 100}.{c % 100:02d}"


def create(gw, amount, key, *, payee=LI_PAYEE, account=ACC, principal=A, rid=None):
    return gw.execute(
        adapt_request("transfer.create", {"account_id": account, "payee_id": payee, "amount_minor": amount},
                      request_id=rid or f"r_{key}", trace_id=f"t_{key}", idempotency_key=key),
        principal_id=principal)


def approve(gw, n, *, decision="approve", token=None, principal=A):
    payload = {"schema_version": "1.1", "challenge_id": n["challenge_id"],
               "operation_digest": n["operation_digest"], "decision": decision}
    if token is not None:
        payload["proof_token"] = token
    return gw.authorize(payload, principal_id=principal)


def finish_confirm(gw, pending, **kw):
    return approve(gw, pending["next_action"], **kw)


def finish_verify(gw, pending, **kw):
    n = pending["next_action"]
    token = gw.issue_demo_proof(n["challenge_id"], principal_id=kw.get("principal", A))
    return approve(gw, n, token=token, **kw)


results = []


def rec(cid, title, lines, ok):
    results.append({"id": cid, "title": title, "ok": ok, "lines": lines})
    print(f"\n=== {cid} {title} -> {'通过' if ok else '失败'}")
    for ln in lines:
        print("   " + ln)


# T01 正常小额转账（确认）
tmp, store, gw, clock = new_bank()
before = {a: balance(gw, a) for a in (ACC, "demo_account_li")}
p = create(gw, 20000, "k_t01")
st1 = p["status"]
done = finish_confirm(gw, p)
after = {a: balance(gw, a) for a in (ACC, "demo_account_li")}
with store.connection() as c:
    cnt = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
           for t in ("transfers", "ledger_entries", "fund_postings")}
ok = (st1 == "awaiting_confirmation" and done["status"] == "succeeded"
      and after[ACC] == 180000 and after["demo_account_li"] == 20000
      and cnt == {"transfers": 1, "ledger_entries": 2, "fund_postings": 2})
rec("B-T01", "正常小额转账（200元，普通确认）", [
    f"转账前余额：A {yuan(before[ACC])}，李明 {yuan(before['demo_account_li'])}",
    f"提交后状态：{st1}（网关要求确认）",
    f"确认后状态：{done['status']}，交易号 {done['data']['transaction_id']}",
    f"转账后余额：A {yuan(after[ACC])}，李明 {yuan(after['demo_account_li'])}",
    f"记账笔数：transfers={cnt['transfers']}，ledger_entries={cnt['ledger_entries']}，fund_postings={cnt['fund_postings']}（双边各一借一贷）"], ok)
tmp.cleanup()

# T02 大额强验证（单笔 1001 元 > 1000）
tmp, store, gw, clock = new_bank()
p = create(gw, 100100, "k_t02")
st = p["status"]
methods = p["next_action"].get("methods")
done = finish_verify(gw, p)
ba = balance(gw)
ok = st == "awaiting_verification" and methods == ["demo_otp"] and done["status"] == "succeeded" and ba == 99900
rec("B-T02", "大额强验证（单笔1001元，含本次日累计>1000）", [
    f"提交后状态：{st}，验证方式 {methods}",
    f"签发模拟OTP并验证后：{done['status']}",
    f"转账后 A 余额 {yuan(ba)}（2000-1001）"], ok)
tmp.cleanup()

# T03 边界恰好 1000 元（confirm）
tmp, store, gw, clock = new_bank()
p = create(gw, 100000, "k_t03")
st = p["status"]
done = finish_confirm(gw, p)
ok = st == "awaiting_confirmation" and done["status"] == "succeeded" and balance(gw) == 100000
rec("B-T03", "日累计边界：恰好1000元（含本次=1000，应普通确认）", [
    f"提交后状态：{st}（100000 分 ≤ 100000 分阈值，走 confirm）",
    f"确认后：{done['status']}；A 余额 {yuan(balance(gw))}"], ok)
tmp.cleanup()

# T04 边界 1000.01 元（verify）
tmp, store, gw, clock = new_bank()
p = create(gw, 100001, "k_t04")
st = p["status"]
done = finish_verify(gw, p)
ok = st == "awaiting_verification" and done["status"] == "succeeded" and balance(gw) == 99999
rec("B-T04", "日累计边界：1000.01元（含本次>1000，应强验证）", [
    f"提交后状态：{st}（100001 分 > 100000 分阈值，走 verify）",
    f"OTP 验证后：{done['status']}；A 余额 {yuan(balance(gw))}"], ok)
tmp.cleanup()

# T05 余额不足拒绝
tmp, store, gw, clock = new_bank()
p = create(gw, 200100, "k_t05")  # 2001 元，A 仅 2000
st = p["status"]
done = finish_verify(gw, p)
ba = balance(gw)
bli = balance(gw, "demo_account_li")
ok = st == "awaiting_verification" and done["status"] == "failed" and done["error"]["code"] == "INSUFFICIENT_FUNDS" and ba == 200000 and bli == 0
rec("B-T05", "余额不足拒绝（转2001元，余额2000）", [
    f"提交后状态：{st}（先过强验证，授权时才查余额）",
    f"验证后：{done['status']} / {done['error']['code']}",
    f"文案：{done['error']['message']}",
    f"余额未变：A {yuan(ba)}，李明 {yuan(bli)}；无扣款"], ok)
tmp.cleanup()

# T06 同名收款人不明（payee.resolve 返回两个小王，不执行）
tmp, store, gw, clock = new_bank()
r = gw.execute(adapt_request("payee.resolve", {"query": "小王", "match_type": "name"},
                             request_id="r_t06", trace_id="t_t06"), principal_id=A)
cands = r["data"]["candidates"]
ok = len(cands) == 2 and {c["payee_id"] for c in cands} == {W1, W2}
lines = [f"按姓名“小王”查询返回 {len(cands)} 个候选："]
for c in cands:
    lines.append(f"  {c['payee_id']}：{c['display_name']} 尾号{c['masked_account']}（{c['bank_name']}）")
lines.append("系统只返回候选，不擅自选定、不发起转账（须用户追问消歧）")
rec("B-T06", "同名收款人不明（两位“小王”）", lines, ok)
tmp.cleanup()

# T07 重复点击幂等（成功后重复提交同键）
tmp, store, gw, clock = new_bank()
p = create(gw, 20000, "k_t07")
finish_confirm(gw, p)
n1 = balance(gw)
replay = create(gw, 20000, "k_t07")  # 完全相同的请求再点一次
n2 = balance(gw)
with store.connection() as c:
    tcnt = c.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]
ok = replay["status"] == "succeeded" and n1 == n2 == 180000 and tcnt == 1
rec("B-T07", "重复点击/重复提交（幂等）", [
    f"首次完成后 A 余额 {yuan(n1)}；再次提交同一请求",
    f"第二次返回：{replay['status']}（重放原结果，不二次记账）",
    f"重复后 A 余额 {yuan(n2)}，transfers 表仍为 {tcnt} 笔"], ok)
tmp.cleanup()

# T08 同键改内容（200 变 300）
tmp, store, gw, clock = new_bank()
create(gw, 20000, "k_t08")
try:
    r = create(gw, 30000, "k_t08")
    code, msg = r["status"], r.get("error", {}).get("code")
    raised = False
except ServiceError as e:
    code, msg = "raised", e.code
    raised = True
ba = balance(gw)
ok = msg == "IDEMPOTENCY_CONFLICT" and ba == 200000
rec("B-T08", "同一幂等键篡改金额（200→300）", [
    "第一笔200元进入待确认；用同一 idempotency_key 提交300元",
    f"结果：{code} / {msg}（拒绝，提示核实原操作）",
    f"A 余额仍为 {yuan(ba)}，未发生转账"], ok)
tmp.cleanup()

# T09 用户主动拒绝
tmp, store, gw, clock = new_bank()
p = create(gw, 20000, "k_t09")
done = finish_confirm(gw, p, decision="reject")
ba, bli = balance(gw), balance(gw, "demo_account_li")
ok = done["status"] == "cancelled" and ba == 200000 and bli == 0
rec("B-T09", "用户在确认页拒绝", [
    f"提交后 {p['status']}；选择拒绝",
    f"结果：{done['status']}；A {yuan(ba)}、李明 {yuan(bli)}，无扣款"], ok)
tmp.cleanup()

# T10 授权 10 分钟过期
tmp, store, gw, clock = new_bank()
t0 = clock.t
p = create(gw, 20000, "k_t10")
exp = p["next_action"]["expires_at"]
clock.t = t0 + timedelta(minutes=10, seconds=1)
try:
    done = finish_confirm(gw, p)
    code = done["error"]["code"]
    status = done["status"]
except ServiceError as e:
    code, status = e.code, "raised"
ba, bli = balance(gw), balance(gw, "demo_account_li")
ok = code == "AUTH_EXPIRED" and status == "failed" and ba == 200000
rec("B-T10", "授权超时（挑战有效期10分钟）", [
    f"挑战签发时间 t0；expires_at={exp}",
    "时钟推进到 t0+10分01秒后确认",
    f"结果：{status} / {code}；原键不续期，需重新发起",
    f"A {yuan(ba)}、李明 {yuan(bli)}，无扣款"], ok)
tmp.cleanup()

# T11 连续 5 次错误验证锁定
tmp, store, gw, clock = new_bank()
p = create(gw, 100100, "k_t11")
n = p["next_action"]
gw.issue_demo_proof(n["challenge_id"], principal_id=A)  # 真实凭证存在，但故意提交错误凭证
codes = []
for i in range(5):
    try:
        r = approve(gw, n, token="wrong-token")
        codes.append(r["status"] + "/" + r.get("error", {}).get("code", ""))
        final = r
    except ServiceError as e:
        codes.append(f"raises/{e.code}")
        final = None
ba = balance(gw)
with store.connection() as c:
    att = c.execute("SELECT failed_attempts,state FROM challenges WHERE challenge_id=?", (n["challenge_id"],)).fetchone()
ok = codes[:4] == ["raises/AUTH_INVALID"] * 4 and final and final["status"] == "blocked" and final["error"]["code"] == "SAFETY_LOCKED" and ba == 200000 and att["state"] == "blocked" and att["failed_attempts"] == 5
rec("B-T11", "连续5次错误模拟验证→锁定本次操作", [
    "前4次：" + "；".join(codes[:4]) + "（业务仍待验证，可继续）",
    f"第5次：{final['status']} / {final['error']['code']}",
    f"文案：{final['error']['message']}",
    f"挑战状态={att['state']}，failed_attempts={att['failed_attempts']}；A 余额 {yuan(ba)}，锁定仅本次操作"], ok)
tmp.cleanup()

# T12 错用户越权授权
tmp, store, gw, clock = new_bank()
p = create(gw, 20000, "k_t12", principal=A)
n = p["next_action"]
try:
    r = approve(gw, n, principal=LI_USER)
    outcome = r["status"] + "/" + r.get("error", {}).get("code", "")
except ServiceError as e:
    outcome = "raises/" + e.code
# 李明自己的失败次数不应增加
with store.connection() as c:
    li_audit = c.execute("SELECT COUNT(*) FROM audit_events WHERE principal_id=? AND outcome='authorization_invalid'", (LI_USER,)).fetchone()[0]
    a_ch = c.execute("SELECT state,failed_attempts FROM challenges WHERE challenge_id=?", (n["challenge_id"],)).fetchone()
ok = "AUTH_INVALID" in outcome and a_ch["state"] == "pending" and a_ch["failed_attempts"] == 0
rec("B-T12", "错用户越权（李明尝试确认 A 的转账）", [
    f"A 创建待确认挑战；李明用自己的身份提交授权 → {outcome}",
    f"凭据无效/不属于该用户；A 的挑战保持 {a_ch['state']}、失败次数 {a_ch['failed_attempts']}",
    f"李明名下拒绝审计 {li_audit} 条；不能代他人确认、不消耗他人失败次数"], ok)
tmp.cleanup()

# T13 刷新/重启恢复（待决操作持久化）
tmp, store, gw, clock = new_bank()
p = create(gw, 20000, "k_t13")
ops_before = gw.list_operations(principal_id=A)
# 模拟重启：丢弃内存对象，用同一 store 新建网关
gw2 = BankGateway(store, clock=clock)
ops_after = gw2.list_operations(principal_id=A)
q = gw2.execute(adapt_request("operation.get",
    {"original_action": "transfer.create", "original_idempotency_key": "k_t13"},
    request_id="r_q", trace_id="t_q"), principal_id=A)
qstatus = q["data"]["operation_result"]["status"]
done = finish_confirm(gw2, p)
ok = (ops_before[0]["status"] == "awaiting_confirmation" and ops_after[0]["status"] == "awaiting_confirmation"
      and qstatus == "awaiting_confirmation" and done["status"] == "succeeded" and balance(gw2) == 180000)
rec("B-T13", "刷新/重启后待决操作恢复（未确认不丢、不自动执行）", [
    f"提交后待确认；list_operations 索引：{ops_before[0]['action']} / {ops_before[0]['status']}",
    "模拟重启（同库新建网关）后再次读取，待决操作仍在",
    f"operation.get 原操作状态：{qstatus}",
    f"重启后确认仍可完成：{done['status']}；A 余额 {yuan(balance(gw2))}"], ok)
tmp.cleanup()

# T14 上海时区跨日累计重置
tmp, store, gw, clock = new_bank(datetime(2026, 10, 15, 15, 59, 0, tzinfo=UTC))  # 上海 23:59
p1 = create(gw, 90000, "k_t14a")
d1 = finish_confirm(gw, p1)
clock.t = datetime(2026, 10, 15, 16, 1, 0, tzinfo=UTC)  # 上海次日 00:01
p2 = create(gw, 20000, "k_t14b")
st2 = p2["status"]
d2 = finish_confirm(gw, p2)
ba = balance(gw)
ok = d1["status"] == "succeeded" and st2 == "awaiting_confirmation" and d2["status"] == "succeeded" and ba == 90000
rec("B-T14", "上海时区跨日：日累计按上海日期归零", [
    "上海 23:59 转 900 元（当日累计900，确认）成功",
    "时钟推进到上海次日 00:01，再转 200 元",
    f"新一日累计重新计算，第二笔状态 {st2}（未因前一天900元而强验证）",
    f"两笔成功，A 余额 {yuan(ba)}（2000-1100）"], ok)
tmp.cleanup()

# T15 越权：使用不归属本人的收款人
tmp, store, gw, clock = new_bank()
try:
    r = create(gw, 10000, "k_t15", account="demo_account_li", payee=LI_PAYEE, principal=LI_USER, rid="r_t15")
    outcome = r["status"] + "/" + r.get("error", {}).get("code", "")
except ServiceError as e:
    outcome = "raises/" + e.code
ok = "FORBIDDEN" in outcome
rec("B-T15", "收款人不属于当前用户（越权映射拒绝）", [
    "李明名下无“李明”收款人映射，发起转账",
    f"结果：{outcome}（收款人不属于当前用户）；无扣款"], ok)
tmp.cleanup()

# T16 授权升级：确认等待期间跨过门槛，旧确认失效转强验证
tmp, store, gw, clock = new_bank()
pa = create(gw, 80000, "k_t16a")  # 800 待确认
pb = create(gw, 80000, "k_t16b")  # 800 待确认
db = finish_confirm(gw, pb)       # 先完成 B：当日累计 800
da = gw.authorize({"schema_version": "1.1", "challenge_id": pa["next_action"]["challenge_id"],
                   "operation_digest": pa["next_action"]["operation_digest"], "decision": "approve"}, principal_id=A)
upgraded = da["status"]
new_n = da["next_action"]
token = gw.issue_demo_proof(new_n["challenge_id"], principal_id=A)
da2 = gw.authorize({"schema_version": "1.1", "challenge_id": new_n["challenge_id"],
                    "operation_digest": new_n["operation_digest"], "decision": "approve",
                    "proof_token": token}, principal_id=A)
ba = balance(gw)
ok = db["status"] == "succeeded" and upgraded == "awaiting_verification" and da2["status"] == "succeeded" and ba == 40000
rec("B-T16", "确认等待期间累计跨门槛→旧确认升级为强验证", [
    "A、B 两笔各800元均待确认（此时各自累计800，普通确认）",
    "先确认 B 成功（当日累计=800）；再确认 A",
    f"授权时复查累计 800+800=1600>1000，旧确认失效，A 升级为：{upgraded}",
    f"对新挑战完成模拟验证后：{da2['status']}；A 余额 {yuan(ba)}（2000-1600）"], ok)
tmp.cleanup()

print("\n\n===== 汇总 =====")
passed = sum(1 for r in results if r["ok"])
print(f"{passed}/{len(results)} 项通过")
Path("b_evidence.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print("证据已写入 b_evidence.json")
