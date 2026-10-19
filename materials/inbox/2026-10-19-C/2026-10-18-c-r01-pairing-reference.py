# -*- coding: utf-8 -*-
"""C1-003-R01 借贷配对「编号闭合」规则参考实现（C 第三轮 C-07 定版配套，2026-10-18）。

【重要声明】本脚本是 C-07 定版规则的证据性参考实现，不是生产代码：
  - billing 生产代码（billing/__init__.py）明确报告“借贷配对……尚未启用”；
  - 本脚本不被任何运行时/网关调用，不改变任何线上结论；
  - 仅用于把 C-07 的文字规则变成可复算的算例，供联合验收与后续 A 接入时对照。

规则（C-07 定版，唯一判定规则，不允许任何“近似推断”）：
  1) 两笔构成同一借贷关系，当且仅当：
     a. 同账户、同收款人 payee_id、方向相反、kind=transfer；
     b. 编号闭合：放款腿编号 X、还款腿编号 Y，满足 X=Y（同一显式配对键）
        或 Y≠X 且还款腿携带“显式指向字段”指向放款腿编号 X；
     c. 禁止仅凭收款人相同 / 金额相近 / 时间接近推断。
  2) 窗口：自放款日（含）起 90 个自然日。
  3) 窗口内归还合计 P 与本金 D：
     P≠D 命中 HIT（Δ=P−D，正负皆报）；P=D 不命中 MISS；
     窗口内无归还（或仅有窗口外归还）→ UNJUDGEABLE（无法判定，不等于无异常）。

复现：python -B -X utf8 docs/evidence/2026-10-18-c-r01-pairing-reference.py
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

WINDOW_DAYS = 90


def leg(tid, account, day, amount, direction, payee, *, ref=None, points_to=None):
    """一条 transfer 腿。ref 为显式配对键（X=Y 用）；points_to 为还款腿指向放款腿编号。"""
    return dict(tid=tid, account=account, day=date.fromisoformat(day), amount=amount,
                direction=direction, payee=payee, ref=ref, points_to=points_to)


def pair(legs, account):
    """在同账户内按编号闭合规则判定；返回 (loan, repayments, verdict, delta)。"""
    loans = [l for l in legs if l["account"] == account and l["direction"] == "debit"]
    rets = [l for l in legs if l["account"] == account and l["direction"] == "credit"]
    results = []
    for loan in loans:
        in_window, closed = [], []
        for r in rets:
            if r["payee"] != loan["payee"]:
                continue
            within = (loan["day"] <= r["day"] <= loan["day"] + timedelta(days=WINDOW_DAYS))
            key_closure = (r["ref"] is not None and r["ref"] == loan["ref"])
            ptr_closure = (r["points_to"] is not None and r["points_to"] == loan["tid"])
            if within and (key_closure or ptr_closure):
                in_window.append(r)
                closed.append(r)
        # 显式记录：窗口外归还（闭合存在但超窗）不参与判定
        out_of_window = [r for r in rets
                         if r["payee"] == loan["payee"]
                         and r["points_to"] == loan["tid"]
                         and r["day"] > loan["day"] + timedelta(days=WINDOW_DAYS)]
        if not closed:
            verdict = "UNJUDGEABLE"
            delta = None
        else:
            p = sum(r["amount"] for r in closed)
            delta = p - loan["amount"]
            verdict = "HIT" if delta != 0 else "MISS"
        results.append(dict(loan=loan["tid"], amount=loan["amount"],
                            closed=[r["tid"] for r in closed],
                            out_of_window=[r["tid"] for r in out_of_window],
                            verdict=verdict, delta=delta))
    return results


cases = []


def show(cid, title, legs, account, expect):
    res = pair(legs, account)
    ok = [(r["verdict"], r["delta"]) for r in res] == [(e[0], e[1]) for e in expect]
    cases.append(dict(id=cid, title=title,
                      actual=[dict(loan=r["loan"], verdict=r["verdict"],
                                   delta_minor=r["delta"], closed=r["closed"],
                                   out_of_window=r["out_of_window"]) for r in res],
                      expect=expect, verdict="符合" if ok else "不符合"))


# SIM 五组（与 docs/evidence/2026-10-18-c-package-check.json 同口径）
show("SIM-01", "TR-SIM-01 借100000/还120000，Δ+20000 命中",
     [leg("TR-SIM-01-O", "ACCT-SIM", "2026-10-01", 100000, "debit", "PAYEE-SIM", ref="TR-SIM-01"),
      leg("TR-SIM-01-I", "ACCT-SIM", "2026-11-01", 120000, "credit", "PAYEE-SIM", ref="TR-SIM-01")],
     "ACCT-SIM", [("HIT", 20000)])

show("SIM-02", "TR-SIM-02 借80000/还80000，不命中",
     [leg("TR-SIM-02-O", "ACCT-SIM", "2026-10-01", 80000, "debit", "PAYEE-SIM", ref="TR-SIM-02"),
      leg("TR-SIM-02-I", "ACCT-SIM", "2026-11-01", 80000, "credit", "PAYEE-SIM", ref="TR-SIM-02")],
     "ACCT-SIM", [("MISS", 0)])

show("SIM-03", "TR-SIM-03 借50000/还50001，Δ+1 命中（窗口 10-01→12-30）",
     [leg("TR-SIM-03-O", "ACCT-SIM", "2026-10-01", 50000, "debit", "PAYEE-SIM", ref="TR-SIM-03"),
      leg("TR-SIM-03-I", "ACCT-SIM", "2026-12-30", 50001, "credit", "PAYEE-SIM", ref="TR-SIM-03")],
     "ACCT-SIM", [("HIT", 1)])

show("SIM-04", "TR-SIM-04 借50000/还50000，不命中",
     [leg("TR-SIM-04-O", "ACCT-SIM", "2026-10-01", 50000, "debit", "PAYEE-SIM", ref="TR-SIM-04"),
      leg("TR-SIM-04-I", "ACCT-SIM", "2026-11-05", 50000, "credit", "PAYEE-SIM", ref="TR-SIM-04")],
     "ACCT-SIM", [("MISS", 0)])

show("SIM-05", "TR-SIM-05 借30000，窗口内无归还→无法判定",
     [leg("TR-SIM-05-O", "ACCT-SIM", "2026-10-01", 30000, "debit", "PAYEE-SIM", ref="TR-SIM-05")],
     "ACCT-SIM", [("UNJUDGEABLE", None)])

# 真实数据：LX-003 与 LX-011，现状（无指向字段）
real_no_ptr = [
    leg("LX-003", "ACCT-C001", "2026-08-10", 100000, "debit", "PAYEE-C001", ref="TR-001"),
    leg("LX-011", "ACCT-C001", "2026-09-08", 150000, "credit", "PAYEE-C001", ref="TR-002"),
]
res = pair(real_no_ptr, "ACCT-C001")
ok = (len(res) == 1 and res[0]["verdict"] == "UNJUDGEABLE" and res[0]["closed"] == [])
cases.append(dict(
    id="REAL-LX-003-011-NOW",
    title="真实 LX-003/LX-011：ref 不同（TR-001/TR-002）且无指向字段→不自动配对、不宣称 Δ+500 命中",
    actual=dict(rule_results=res,
                manual_candidates=["LX-003(08-10 借100000, 小王甲)", "LX-011(09-08 入账150000, 小王甲)"],
                manual_note="仅列入人工核查候选，不得由系统自动报异常"),
    expect="两笔均不闭合；无 HIT；候选人工核查",
    verdict="符合" if ok else "不符合"))

# 补指向字段后（同一真实数据，补 link_to=LX-003 表示还款腿指向放款腿）
real_patched = [
    leg("LX-003", "ACCT-C001", "2026-08-10", 100000, "debit", "PAYEE-C001", ref="TR-001"),
    leg("LX-011", "ACCT-C001", "2026-09-08", 150000, "credit", "PAYEE-C001", ref="TR-002",
        points_to="LX-003"),
]
res = pair(real_patched, "ACCT-C001")
ok = res[0]["verdict"] == "HIT" and res[0]["delta"] == 50000
cases.append(dict(
    id="REAL-LX-003-011-PATCHED",
    title="补充显式指向字段后：窗口 08-10→11-08 含 09-08，Δ=+50000 分（+500.00 元）命中",
    actual=res, expect="HIT, delta=+50000",
    verdict="符合" if ok else "不符合"))

# 反例一：收款人相同、金额相近、时间接近，但无闭合 → 禁止推断
infer = [
    leg("T1", "ACCT-X", "2026-09-01", 50000, "debit", "PAYEE-X"),
    leg("T2", "ACCT-X", "2026-09-20", 50000, "credit", "PAYEE-X"),
]
res = pair(infer, "ACCT-X")
ok = res[0]["verdict"] == "UNJUDGEABLE"
cases.append(dict(id="CTRL-NO-INFER",
                  title="对照：同收款人同金额同月但无任何闭合编号 → 仍 UNJUDGEABLE，禁止推断",
                  actual=res, expect="UNJUDGEABLE",
                  verdict="符合" if ok else "不符合"))

# 反例二：闭合但归还落在窗口外 → UNJUDGEABLE
late = [
    leg("L1", "ACCT-Y", "2026-08-01", 30000, "debit", "PAYEE-Y"),
    leg("L2", "ACCT-Y", "2026-11-02", 30000, "credit", "PAYEE-Y", points_to="L1"),
]
res = pair(late, "ACCT-Y")
ok = res[0]["verdict"] == "UNJUDGEABLE" and res[0]["out_of_window"] == ["L2"]
cases.append(dict(id="CTRL-OUT-OF-WINDOW",
                  title="对照：闭合但还款日 11-02 超过 90 天窗口（08-01→10-30）→ 无法判定",
                  actual=res, expect="UNJUDGEABLE 且 out_of_window=[L2]",
                  verdict="符合" if ok else "不符合"))

# 反例三：少归还（Δ 为负）也要命中
under = [
    leg("U1", "ACCT-Z", "2026-09-01", 60000, "debit", "PAYEE-Z", ref="U"),
    leg("U2", "ACCT-Z", "2026-09-20", 59000, "credit", "PAYEE-Z", ref="U"),
]
res = pair(under, "ACCT-Z")
ok = res[0]["verdict"] == "HIT" and res[0]["delta"] == -1000
cases.append(dict(id="CTRL-NEG-DELTA",
                  title="对照：少归还 1000 分（Δ=−1000）同样命中",
                  actual=res, expect="HIT, delta=-1000",
                  verdict="符合" if ok else "不符合"))

summary = dict(
    package="C1-003-R01 借贷配对「编号闭合」参考实现（非生产代码，R01 未启用）",
    executor="C", date="2026-10-18",
    rule_version="C-07 定版（2026-10-18）",
    window="放款日（含）起 90 自然日",
    cases=cases,
    pass_count=sum(1 for c in cases if c["verdict"] == "符合"),
    fail_count=sum(1 for c in cases if c["verdict"] != "符合"),
)
if __name__ == "__main__":
    out = Path(__file__).with_name("2026-10-18-c-r01-pairing-reference.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for c in cases:
        print(c["id"], "->", c["verdict"])
    print("符合", summary["pass_count"], "不符合", summary["fail_count"], "共", len(cases))
    print("证据输出：", out)
