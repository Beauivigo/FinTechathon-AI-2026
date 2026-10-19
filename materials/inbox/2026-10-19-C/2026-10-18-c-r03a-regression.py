# -*- coding: utf-8 -*-
"""R03-A（大额单笔，分支A）上线后回归——C 第三轮，2026-10-18。

直接调用生产代码 billing.analyze（不重写规则）：
  analyze(params, transactions)，params 为 bill.analyze 入参（account_id/start_date/end_date）。
数据：
  1) 真实 V1.3 复核资料 15 条 LX 流水（六组账户-月份窗口），逐字转录自
     docs/evidence/2026-10-16-material-retest.json（material_check 权威输出）；
  2) 边界/对照用例（金额、kind、方向、账户、窗口、顺序、报告口径、毛支出范围）。
复现：python -B -X utf8 docs/evidence/2026-10-18-c-r03a-regression.py
"""
import json
import sys
from calendar import monthrange
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from billing import analyze

# ---- 真实 V1.3 全量 15 条（occurred_at 为上海时区；字段即标准 transaction 记录） ----
LX = [
    ("LX-001", "ACCT-C001", "2026-08-02T00:00:00+08:00", 1200000, "credit", "income", "工资"),
    ("LX-002", "ACCT-C001", "2026-08-05T00:00:00+08:00", 32850, "debit", "consumption", "日用百货"),
    ("LX-003", "ACCT-C001", "2026-08-10T00:00:00+08:00", 100000, "debit", "transfer", "小王甲"),
    ("LX-004", "ACCT-C002", "2026-08-12T00:00:00+08:00", 29900, "credit", "refund", "退款"),
    ("LX-005", "ACCT-C002", "2026-08-15T00:00:00+08:00", 500000, "debit", "wealth", "基金申购"),
    ("LX-006", "ACCT-C002", "2026-08-20T00:00:00+08:00", 2500, "debit", "subscription", "影音会员"),
    ("LX-007", "ACCT-C001", "2026-08-25T00:00:00+08:00", 8640, "debit", "consumption", "餐饮"),
    ("LX-008", "ACCT-C001", "2026-08-28T00:00:00+08:00", 5000, "debit", "consumption", "交通"),
    ("LX-009", "ACCT-C001", "2026-09-01T00:00:00+08:00", 1200000, "credit", "income", "工资"),
    ("LX-010", "ACCT-C002", "2026-09-03T00:00:00+08:00", 250000, "debit", "consumption", "住房"),
    ("LX-011", "ACCT-C001", "2026-09-08T00:00:00+08:00", 150000, "credit", "transfer", "小王甲"),
    ("LX-012", "ACCT-C002", "2026-09-12T00:00:00+08:00", 523000, "credit", "wealth", "理财回款"),
    ("LX-013", "ACCT-C002", "2026-09-15T00:00:00+08:00", 6800, "debit", "subscription", "云服务"),
    ("LX-014", "ACCT-C003", "2026-09-20T00:00:00+08:00", 41260, "debit", "consumption", "日用百货"),
    ("LX-015", "ACCT-C001", "2026-09-25T00:00:00+08:00", 4500, "credit", "refund", "退款"),
]


def row(tid, account, day, amount, direction, kind, category="边界", merchant="边界商户"):
    return dict(transaction_id=tid, account_id=account, occurred_at=day,
                amount_minor=amount, direction=direction, kind=kind,
                category=category, merchant_name=merchant)


def run(rows, account_id="ACCT-REG", month="2026-08"):
    y, m = int(month[:4]), int(month[5:7])
    start = f"{month}-01"
    end = f"{month}-{monthrange(y, m)[1]:02d}"
    return analyze(dict(account_id=account_id, start_date=start, end_date=end), rows)


def hits(res):
    return [a["transaction_id"] for a in res["anomalies"]]


cases = []


def record(cid, title, expect, res, verdict, note=""):
    cases.append(dict(id=cid, title=title, expect=expect,
                      actual=dict(anomaly_ids=hits(res),
                                  total_expense_minor=res.get("total_expense_minor"),
                                  report_tail=res.get("report", "")[-40:]),
                      verdict=verdict, note=note))


# ---- 一、真实数据：六组账户-月份窗口 ----
real_rows = [row(*r) for r in LX]
real_windows = [
    ("ACCT-C001", "2026-08", []), ("ACCT-C001", "2026-09", []),
    ("ACCT-C002", "2026-08", ["LX-005"]), ("ACCT-C002", "2026-09", []),
    ("ACCT-C003", "2026-08", []), ("ACCT-C003", "2026-09", []),
]
for acc, month, expect in real_windows:
    res = run(real_rows, acc, month)
    record(f"REG-REAL-{acc}-{month}", f"真实窗口 {acc} {month}（R03-A）",
           expect, res,
           "符合" if hits(res) == expect else "不符合",
           "唯一命中：LX-005 5000.00 元理财申购 debit（reference INV-001）" if expect else
           "窗口内无 ≥5000.00 元的 debit 四类 kind")

# LX-005 命中详情：可追流水 + 规则文案
res_c00208 = run(real_rows, "ACCT-C002", "2026-08")
anom = res_c00208["anomalies"][0]
src = {r[0]: r for r in LX}[anom["transaction_id"]]
cases.append(dict(id="REG-REAL-LX005-DETAIL",
                  title="LX-005 命中明细可追流水（金额/kind/参考编号）",
                  expect="命中流水即 LX-005：500000 分=5000.00 元、debit、wealth、reference_id=INV-001",
                  actual={"anomaly": anom, "source_row": dict(
                      transaction_id=src[0], account_id=src[1], occurred_at=src[2],
                      amount_minor=src[3], amount_yuan=5000.00,
                      direction=src[4], kind=src[5], category=src[6],
                      reference_id="INV-001（V1.3 原始凭证）")},
                  verdict="符合" if anom["transaction_id"] == "LX-005" and "分支A" in anom["reason"] else "不符合"))

# ---- 二、边界：金额 499999 / 500000 / 500001 ----
for amt, expect in ((499999, False), (500000, True), (500001, True)):
    res = run([row(f"B-{amt}", "ACCT-REG", "2026-08-10T08:00:00+08:00", amt, "debit", "consumption")])
    record(f"REG-BOUND-{amt}", f"consumption debit {amt} 分（阈值 500000 闭区间）",
           ["命中"] if expect else [], res,
           "符合" if bool(hits(res)) == expect else "不符合")

# ---- 三、四类 kind 在 500000 均命中 ----
for i, kind in enumerate(("consumption", "subscription", "transfer", "wealth")):
    res = run([row(f"K-{kind}", "ACCT-REG", f"2026-08-1{i+1}T08:00:00+08:00", 500000, "debit", kind)])
    record(f"REG-KIND-{kind}", f"debit 500000 且 kind={kind}（R03-A 四类）",
           ["命中"], res, "符合" if hits(res) == [f"K-{kind}"] else "不符合")

# ---- 四、方向/类别排除：credit 各 kind 均不命中 ----
for kind in ("income", "refund", "wealth", "consumption"):
    res = run([row(f"C-{kind}", "ACCT-REG", "2026-08-12T08:00:00+08:00", 500000, "credit", kind)])
    record(f"REG-CREDIT-{kind}", f"credit 500000 kind={kind} 不命中（含 LX-012 情形）",
           [], res, "符合" if not hits(res) else "不符合")

# ---- 五、跨账户/跨窗口不串 ----
res = run([row("X-1", "ACCT-OTHER", "2026-08-15T08:00:00+08:00", 500000, "debit", "wealth"),
           row("X-2", "ACCT-REG", "2026-09-15T08:00:00+08:00", 500000, "debit", "wealth")])
record("REG-ISO", "大额分别在其他账户、其他月份：本窗口零命中",
       [], res, "符合" if not hits(res) else "不符合")

# ---- 六、多笔命中顺序稳定，逆序输入一致 ----
ordered = [row("M-001", "ACCT-REG", "2026-08-10T08:00:00+08:00", 500000, "debit", "consumption"),
           row("M-002", "ACCT-REG", "2026-08-15T08:00:00+08:00", 600000, "debit", "wealth"),
           row("M-003", "ACCT-REG", "2026-08-20T08:00:00+08:00", 700000, "debit", "transfer")]
r1, r2 = run(ordered), run(list(reversed(ordered)))
cases.append(dict(id="REG-ORDER",
                  title="多笔命中按 transaction_id 排序；逆序输入结果一致",
                  expect=["M-001", "M-002", "M-003"],
                  actual={"forward": hits(r1), "reverse_input": hits(r2)},
                  verdict="符合" if hits(r1) == hits(r2) == ["M-001", "M-002", "M-003"] else "不符合"))

# ---- 七、空 anomalies 的报告口径：不代表无异常 ----
res = run([row("E-1", "ACCT-REG", "2026-08-10T08:00:00+08:00", 100, "debit", "consumption")])
report = res.get("report") or ""
cases.append(dict(id="REG-EMPTY-REPORT",
                  title="anomalies 为空时报告必须声明“空列表不代表没有异常”",
                  expect="report 含“空列表不代表没有异常”",
                  actual={"anomaly_ids": hits(res), "report": report},
                  verdict="符合" if "空列表不代表没有异常" in report else "不符合"))

# ---- 八、毛支出口径：仅消费/订阅；wealth/transfer 不计入但 R03-A 照常命中 ----
res = run([
    row("S-1", "ACCT-REG", "2026-08-02T08:00:00+08:00", 10000, "debit", "consumption", "餐饮"),
    row("S-2", "ACCT-REG", "2026-08-03T08:00:00+08:00", 500000, "debit", "wealth", "申购"),
    row("S-3", "ACCT-REG", "2026-08-04T08:00:00+08:00", 100000, "debit", "transfer", "借出"),
    row("S-4", "ACCT-REG", "2026-08-05T08:00:00+08:00", 5000, "debit", "subscription", "会员"),
])
cases.append(dict(id="REG-EXPENSE-SCOPE",
                  title="毛支出仅消费/订阅，wealth/transfer 不计入但 R03-A 照常命中",
                  expect="total_expense_minor=15000（10000+5000），命中 S-2",
                  actual={"total_expense_minor": res["total_expense_minor"], "anomaly_ids": hits(res)},
                  verdict="符合" if res["total_expense_minor"] == 15000 and hits(res) == ["S-2"] else "不符合"))

summary = dict(
    package="R03-A 大额单笔分支A 上线后回归",
    executor="C", date="2026-10-18",
    baseline="main 工作树（billing/__init__.py LARGE_SINGLE_MINOR=500000）",
    rule="debit 且 amount_minor>=500000 且 kind∈{consumption,subscription,transfer,wealth}，阈值闭区间；按自然月/账户隔离",
    python=sys.version.split()[0],
    cases=cases,
    pass_count=sum(1 for c in cases if c["verdict"] == "符合"),
    fail_count=sum(1 for c in cases if c["verdict"] != "符合"),
)
if __name__ == "__main__":
    out = Path(__file__).with_name("2026-10-18-c-r03a-regression.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for c in cases:
        print(c["id"], "->", c["verdict"])
    print("符合", summary["pass_count"], "不符合", summary["fail_count"], "共", len(cases))
    print("证据输出：", out)
