# -*- coding: utf-8 -*-
"""C1-003-R03 分支B（账户日常大额）参考实现（C 第三轮 C-07 定版配套，2026-10-18）。

【重要声明】本脚本是 C-07 定版规则的证据性参考实现，不是生产代码：
  - billing 生产代码仅启用 R03 分支A；分支B 未启用、不产出任何真实结论；
  - 本脚本不被任何运行时/网关调用；
  - 仅用于把 C-07 的文字规则（含两道闸门）变成可复算算例，供联合验收与后续接入对照。

分支B 定版口径（上海日期，T=候选笔所在自然日）：
  1) 基准区间：候选笔所在自然月 1 日至 T−1（含）；禁止使用 T 日及之后的流水（未来数据）。
  2) 基准样本（分子）：同账户、debit、kind∈{consumption,subscription,transfer,wealth}，
     剔除：R01 已识别的放款腿/借入本金腿、工资(income)、退款(refund)、还款腿。
     订阅不剔除。
  3) 样本闸门：区间内符合口径的基准笔数 ≥ 3；不足 3 笔 → NO_CONCLUSION（不产生结论，
     不等同于“无异常”）。
  4) 基准日均 = 分子 ÷ (T 日 − 1)；阈值 = max(10 × 基准日均, 10000 分)（100 元下限）。
  5) 候选金额 ≥ 阈值（闭区间）→ HIT；否则 MISS。样本不足时只有 NO_CONCLUSION。

复现：python -B -X utf8 docs/evidence/2026-10-18-c-r03b-reference.py
"""
import json
import sys
from datetime import date
from pathlib import Path

ELIGIBLE = {"consumption", "subscription", "transfer", "wealth"}
FLOOR_MINOR = 10000
SAMPLE_MIN = 3


def t(day, amount, direction, kind, *, excluded=False):
    """基准流水；excluded=True 表示 R01 已识别的放款/借入本金腿（须剔除）。"""
    return dict(day=date.fromisoformat(day), amount=amount, direction=direction,
                kind=kind, excluded=excluded)


def branch_b(candidate, baseline, t_day):
    """返回 (verdict, detail)。t_day 为候选笔日期（须与基准同自然月）。"""
    tdate = date.fromisoformat(t_day)
    first = tdate.replace(day=1)
    eligible = [b for b in baseline
                if first <= b["day"] < tdate
                and b["direction"] == "debit"
                and b["kind"] in ELIGIBLE
                and not b["excluded"]]
    n = len(eligible)
    if n < SAMPLE_MIN:
        return "NO_CONCLUSION", dict(reason=f"基准样本仅 {n} 笔（<3），不产生结论",
                                     sample=n, numerator=sum(b["amount"] for b in eligible),
                                     denominator=tdate.day - 1)
    numerator = sum(b["amount"] for b in eligible)
    denom = tdate.day - 1
    daily_avg = numerator // denom
    threshold = max(10 * daily_avg, FLOOR_MINOR)
    verdict = "HIT" if candidate >= threshold else "MISS"
    return verdict, dict(sample=n, numerator=numerator, denominator=denom,
                         daily_avg_floor=daily_avg, threshold_minor=threshold)


cases = []


def add(cid, title, expect, actual, verdict):
    actual = dict(actual)
    actual["verdict"] = actual.get("verdict", "")
    cases.append(dict(id=cid, title=title, expect=expect, actual=actual,
                      verdict="符合" if verdict else "不符合"))


# ---- 一、真实 LX-005：T=2026-08-15，T−1 基准为空 → B 不产生结论（仅 A 命中） ----
# C002 08-01..08-14：LX-004 为 credit refund（剔除），无其他 debit；LX-006 在 08-20，属未来，禁用。
baseline_real = [
    t("2026-08-12", 29900, "credit", "refund"),  # LX-004，方向 credit 剔除
]
v, d = branch_b(500000, baseline_real, "2026-08-15")
add("REAL-LX005-B-NEW",
    "LX-005（08-15，500000）按 C-07 定版 T−1 口径：B 基准为空，不产生结论",
    "NO_CONCLUSION（A 命中、B 不产生结论；不得把 B 写成无异常）",
    dict(verdict=v, **d), v == "NO_CONCLUSION")

# ---- 二、C-08 旧算法复现（反面教材）：全月 31 天含未来 LX-006，无 100 元下限 → 阈值 806 ----
# 旧口径（已废弃）：分子=LX-006 2500 分（08-20，晚于 T，未来数据）；分母 31；
# 2500//31=80，10×80=800；按四舍五入口径 80.6→806 分（无下限）；500000≥806 → 误报 HIT。
old_daily = 2500 / 31
old_threshold = round(10 * old_daily)
add("REAL-LX005-B-OLD-C08",
    "C-08 旧算法复现（反面）：用 08-20 未来流水+全月 31 天+无下限，得阈值约 806 分 → 误判命中",
    "该口径违反 T−1（使用未来数据），已被 C-07 定版废弃；仅作对照，不产生结论",
    dict(old_numerator=2500, old_denominator=31,
         old_daily_avg=round(old_daily, 2), old_threshold_approx=old_threshold,
         would_have_hit=500000 >= old_threshold,
         future_data_used="LX-006 2026-08-20 > T=2026-08-15"),
    True)

# ---- 三、SIM-C-019 / SIM-C-025：100 元下限与闭区间自洽 ----
# T=08-11，基准 08-01..08-10 每日 1 笔 1000 分 debit（10 笔）：日均 10000/10=1000，阈值 10000。
base_sim = [t(f"2026-08-{d:02d}", 1000, "debit", "consumption") for d in range(1, 11)]
v19, d19 = branch_b(10000, base_sim, "2026-08-11")
v25, d25 = branch_b(9999, base_sim, "2026-08-11")
add("SIM-C-019", "SIM-C-019 候选 10000 分（日均 1000 分，阈值 10000）命中（闭区间）",
    "HIT, threshold=10000", dict(verdict=v19, **d19),
    v19 == "HIT" and d19["threshold_minor"] == 10000)
add("SIM-C-025", "SIM-C-025 候选 9999 分，低于阈值 10000 不命中",
    "MISS", dict(verdict=v25, **d25), v25 == "MISS")

# ---- 四、样本闸门：仅 2 笔基准时，大额候选也不产生结论 ----
base_two = [t("2026-09-01", 1000, "debit", "consumption"),
            t("2026-09-02", 1000, "debit", "consumption")]
v, d = branch_b(999999, base_two, "2026-09-10")
add("SIM-GATE-LT3", "基准样本仅 2 笔：候选 999999 也只能 NO_CONCLUSION（不是 MISS）",
    "NO_CONCLUSION", dict(verdict=v, **d), v == "NO_CONCLUSION")

# ---- 五、下限闸门：基准很小 → 阈值被 100 元下限抬住 ----
base_floor = [t("2026-08-01", 100, "debit", "consumption"),
              t("2026-08-02", 100, "debit", "consumption"),
              t("2026-08-03", 100, "debit", "consumption")]
vlo, dlo = branch_b(9999, base_floor, "2026-08-11")
vhi, dhi = branch_b(10000, base_floor, "2026-08-11")
add("SIM-FLOOR-9999", "基准日均 30 分（300/10），10×=300，阈值由下限抬到 10000；9999 不命中",
    "MISS, threshold=10000", dict(verdict=vlo, **dlo),
    vlo == "MISS" and dlo["threshold_minor"] == 10000)
add("SIM-FLOOR-10000", "同上基准，候选 10000 分命中（闭区间）",
    "HIT", dict(verdict=vhi, **dhi), vhi == "HIT")

# ---- 六、口径：订阅计入基准；退款/工资/放款腿剔除 ----
base_mix = [
    t("2026-08-01", 1000, "debit", "consumption"),
    t("2026-08-02", 2500, "debit", "subscription"),  # 订阅不剔除
    t("2026-08-03", 500, "debit", "consumption"),
    t("2026-08-04", 1200000, "credit", "income"),     # 工资剔除
    t("2026-08-05", 29900, "credit", "refund"),       # 退款剔除
    t("2026-08-06", 100000, "debit", "transfer", excluded=True),  # R01 放款腿剔除
]
v, d = branch_b(999999, base_mix, "2026-08-11")
# 分子=1000+2500+500=4000；分母 10；日均 400；阈值=max(4000,10000)=10000；999999 命中
add("SIM-SCOPE-MIX",
    "基准口径：订阅计入；工资/退款/R01 放款腿剔除；分子 4000、阈值 10000、999999 命中",
    "HIT, numerator=4000, threshold=10000", dict(verdict=v, **d),
    v == "HIT" and d["numerator"] == 4000 and d["threshold_minor"] == 10000)

summary = dict(
    package="C1-003-R03 分支B 参考实现（非生产代码，分支B 未启用）",
    executor="C", date="2026-10-18",
    rule_version="C-07 定版（2026-10-18）：T−1 基准 + 样本闸门≥3 + 100 元下限",
    cases=cases,
    pass_count=sum(1 for c in cases if c["verdict"] == "符合"),
    fail_count=sum(1 for c in cases if c["verdict"] != "符合"),
)
if __name__ == "__main__":
    out = Path(__file__).with_name("2026-10-18-c-r03b-reference.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for c in cases:
        print(c["id"], "->", c["verdict"])
    print("符合", summary["pass_count"], "不符合", summary["fail_count"], "共", len(cases))
    print("证据输出：", out)
