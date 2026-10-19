# -*- coding: utf-8 -*-
"""C 第四轮交付包自查。

在仓库根目录运行：
    py -X utf8 materials/inbox/2026-10-20-C/check_c4.py

只依赖仓库现有纯函数（agent.routing.route、billing.analyze）与标准库。
输出 selfcheck.json；存在失败时退出码为 1。
"""
import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PKG = Path(__file__).resolve().parent


def find_repo_root(start):
    for candidate in [start, *start.parents]:
        if (candidate / "agent/routing.py").is_file() and (candidate / "billing/__init__.py").is_file():
            return candidate
    env = os.environ.get("C4_REPO_ROOT", "").strip()
    if env and (Path(env) / "agent/routing.py").is_file():
        return Path(env)
    return None


ROOT = find_repo_root(PKG) or find_repo_root(Path.cwd())
if ROOT is None:
    print("无法定位仓库根目录：请把本包放回 materials/inbox/2026-10-20-C，"
          "或在仓库根目录运行，或设置 C4_REPO_ROOT 指向仓库根目录。")
    raise SystemExit(2)
sys.path.insert(0, str(ROOT))

SHANGHAI = timezone(timedelta(hours=8))
FULL_LOG = ROOT / "docs/evidence/2026-10-19-A4-S3-full.txt"
CASE_FILE = PKG / "02_跨模块用例12条.json"
ENTRY_FILE = PKG / "03_入口输入24句.json"
DECISION_FILE = PKG / "01_C-07定版.md"

failures = []


def fail(scope, message):
    failures.append({"scope": scope, "message": message})


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        fail(str(path.name), f"无法读取/解析: {exc}")
        return None


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


cases_doc = load(CASE_FILE)
entry_doc = load(ENTRY_FILE)
decision = DECISION_FILE.read_text(encoding="utf-8") if DECISION_FILE.exists() else ""
full_log = FULL_LOG.read_text(encoding="utf-8", errors="replace") if FULL_LOG.exists() else ""

# ---------------------------------------------------------------- decisions
decisions_present = ("仅导入资料" in decision) and ("继续保留为参考，不启用" in decision)
if not decisions_present:
    fail("decisions", "01_C-07定版.md 未同时给出 R01=仅导入资料、R03-B=不启用")

# ---------------------------------------------------------------- errata
errata_file = PKG / "06_勘误遵循核对.md"
entry_md_file = PKG / "03_入口输入24句.md"
ref_file = PKG / "04_R01-R03B范围与引用.md"
errata_text = errata_file.read_text(encoding="utf-8") if errata_file.exists() else ""
entry_md = entry_md_file.read_text(encoding="utf-8") if entry_md_file.exists() else ""
ref_text = ref_file.read_text(encoding="utf-8") if ref_file.exists() else ""
errata_checks = {
    "errata_file": bool(errata_text) and all(tag in errata_text for tag in ("C-12", "C-13", "C-14", "C-15", "C-16")),
    "c12_no_static_cell": "| 同预期 |" not in entry_md,
    "c13_exact_threshold": ("精确分数" in decision) and ("ceil" in decision),
    "c14_window": ("0 至 90" in decision) or ("[0, 90]" in ref_text),
    "c15_simulated": "原始模拟资料" in ref_text,
}
for key, ok in errata_checks.items():
    if not ok:
        fail("errata", f"勘误遵循检查未通过: {key}")

# ---------------------------------------------------------------- 12 cases
cases = cases_doc.get("cases", []) if cases_doc else []
if len(cases) != 12:
    fail("cases", f"用例数量应为 12，实际 {len(cases)}")

ids = [case.get("id") for case in cases]
if len(set(ids)) != len(ids):
    fail("cases", f"用例 id 重复: {ids}")

groups = {}
for case in cases:
    groups.setdefault(case.get("group"), []).append(case.get("id"))
for group, expected in (("商户/预算/账单", 4), ("申请/订阅取消/提醒", 4), ("定时/恢复/入口", 4)):
    actual = len(groups.get(group, []))
    if actual != expected:
        fail("cases", f"分组 {group} 应为 {expected} 条，实际 {actual}")

required_case_keys = ["id", "group", "title", "preconditions", "steps", "expected", "actual", "evidence", "rule_refs"]
for case in cases:
    missing = [key for key in required_case_keys if key not in case or case[key] in (None, "", [], {})]
    if missing:
        fail(case.get("id", "?"), f"缺少字段或为空: {missing}")
    if "verdict" in case.get("actual", {}) or "verdict" in case:
        fail(case.get("id", "?"), "actual 不允许写死 verdict/通过标签")
    for path_text in case.get("evidence", []) + case.get("rule_refs", []):
        path = (ROOT / path_text).resolve()
        if not path.exists():
            fail(case.get("id", "?"), f"引用路径不存在: {path_text}")
    actual = case.get("actual", {})
    status = str(actual.get("status", ""))
    if not status.startswith(("已执行", "待执行")):
        fail(case.get("id", "?"), f"actual.status 不合法: {status}")
    if status.startswith("已执行") and actual.get("executed_by") == "A":
        if not actual.get("tests"):
            fail(case.get("id", "?"), "A 执行的用例缺少 tests 引用")
        if actual.get("evidence_log") and not (ROOT / actual["evidence_log"]).exists():
            fail(case.get("id", "?"), f"evidence_log 不存在: {actual['evidence_log']}")
        for test_name in actual.get("tests", []):
            if test_name not in full_log:
                fail(case.get("id", "?"), f"全量日志中没有测试名: {test_name}")
    if status.startswith("已执行") and actual.get("executed_by") == "C":
        if not actual.get("command") or not actual.get("environment"):
            fail(case.get("id", "?"), "C 执行用例缺少 command/environment")
    if not case.get("expected"):
        fail(case.get("id", "?"), "缺少 expected")

expected_blobs = [json.dumps(case.get("expected"), ensure_ascii=False, sort_keys=True) for case in cases if case.get("expected")]
if len(set(expected_blobs)) != len(expected_blobs):
    fail("cases", "存在完全相同的 expected，需保证每条预期唯一")

# ---- executable case: bill.analyze
bill_case = next((case for case in cases if case.get("id") == "C4-BILL-01"), None)
bill_match = False
bill_error = None
if bill_case is None:
    fail("bill", "缺少 C4-BILL-01")
else:
    try:
        from billing import analyze  # noqa: PLC0415
        bill_actual = analyze(bill_case["input"]["params"], bill_case["input"]["rows"])
        expected = bill_case["expected"]
        bill_match = all(
            bill_actual.get(key) == expected.get(key)
            for key in ("total_expense_minor", "categories", "anomalies", "report")
        )
        if not bill_match:
            fail("bill", "billing.analyze 实跑结果与 expected 不一致")
        bill_error = None
    except Exception as exc:  # noqa: BLE001
        bill_error = f"{type(exc).__name__}: {exc}"
        fail("bill", f"billing.analyze 执行失败: {bill_error}")

# ---------------------------------------------------------------- 24 entries
entries = entry_doc.get("cases", []) if entry_doc else []
if len(entries) != 24:
    fail("entry", f"入口输入应为 24 句，实际 {len(entries)}")

entry_ids = [item.get("id") for item in entries]
if len(set(entry_ids)) != len(entry_ids):
    fail("entry", "入口输入 id 重复")
texts = [item.get("text") for item in entries]
if len(set(texts)) != len(texts):
    fail("entry", "入口输入存在重复句子")

category_counts = {}
type_counts = {}
for item in entries:
    category_counts[item.get("category")] = category_counts.get(item.get("category"), 0) + 1
    type_counts[item.get("type")] = type_counts.get(item.get("type"), 0) + 1
for category in ("账单", "转账", "卡片", "AA", "订阅", "生日"):
    if category_counts.get(category) != 4:
        fail("entry", f"类别 {category} 应为 4 句，实际 {category_counts.get(category)}")
for kind in ("正常", "缺字段", "歧义", "取消/否定"):
    if type_counts.get(kind) != 6:
        fail("entry", f"类型 {kind} 应为 6 句，实际 {type_counts.get(kind)}")

try:
    from agent.routing import route  # noqa: PLC0415
except Exception as exc:  # noqa: BLE001
    route = None
    fail("entry", f"无法导入 agent.routing.route: {exc}")

entry_failures = []
if route is not None:
    for item in entries:
        item_id = item.get("id", "?")
        text = item.get("text", "")
        expected = item.get("expected", {})
        actual = item.get("actual", {})
        try:
            rerun = route(text)
        except Exception as exc:  # noqa: BLE001
            entry_failures.append({"id": item_id, "reason": f"route 抛错: {exc}"})
            continue
        problems = []
        if rerun.get("status") != expected.get("status"):
            problems.append(f"status {rerun.get('status')} != {expected.get('status')}")
        if rerun.get("intent") != expected.get("intent"):
            problems.append(f"intent {rerun.get('intent')} != {expected.get('intent')}")
        if rerun.get("fields") != expected.get("fields"):
            problems.append(f"fields {rerun.get('fields')} != {expected.get('fields')}")
        if sorted(rerun.get("missing", [])) != sorted(expected.get("missing", [])):
            problems.append(f"missing {rerun.get('missing')} != {expected.get('missing')}")
        keyword = expected.get("message_contains")
        if keyword and keyword not in rerun.get("message", ""):
            problems.append(f"message 不含 {keyword!r}")
        if actual != rerun:
            problems.append("记录 actual 与本次重跑不一致（结果漂移）")
        if problems:
            entry_failures.append({"id": item_id, "reason": "; ".join(problems)})
if entry_failures:
    for item in entry_failures:
        fail("entry", f"{item['id']}: {item['reason']}")

# ---- executable case: entry route (C4-ENTRY-01)
entry_case = next((case for case in cases if case.get("id") == "C4-ENTRY-01"), None)
entry_match = False
if entry_case is None or route is None:
    fail("entry_case", "缺少 C4-ENTRY-01 或 route 不可用")
else:
    ready = route(entry_case["input"]["ready_text"])
    missing = route(entry_case["input"]["missing_text"])
    entry_match = (
        ready.get("status") == entry_case["expected"]["ready"]["status"]
        and ready.get("intent") == entry_case["expected"]["ready"]["intent"]
        and ready.get("fields") == entry_case["expected"]["ready"]["fields"]
        and sorted(ready.get("missing", [])) == sorted(entry_case["expected"]["ready"]["missing"])
        and missing.get("status") == entry_case["expected"]["missing"]["status"]
        and missing.get("intent") == entry_case["expected"]["missing"]["intent"]
        and missing.get("fields") == entry_case["expected"]["missing"]["fields"]
        and sorted(missing.get("missing", [])) == sorted(entry_case["expected"]["missing"]["missing"])
    )
    if not entry_match:
        fail("entry_case", "C4-ENTRY-01 的 route 实跑与 expected 不一致")

# ---------------------------------------------------------------- evidence
evidence_missing = []
for case in cases:
    for path_text in case.get("evidence", []):
        path = (ROOT / path_text).resolve()
        if not path.exists():
            evidence_missing.append(f"{case.get('id')}:{path_text}")
if evidence_missing:
    fail("evidence", f"证据路径不存在: {evidence_missing}")

# ---------------------------------------------------------------- summary
result = {
    "package": "C 第四轮交付包（2026-10-20）",
    "generated_at": datetime.now(SHANGHAI).isoformat(),
    "baseline": "main@22473aa",
    "python": sys.version,
    "platform": platform.platform(),
    "checks": {
        "cases_total": len(cases),
        "case_groups": {group: len(items) for group, items in groups.items()},
        "case_ids_unique": len(set(ids)) == len(ids),
        "decisions_present": decisions_present,
        "errata_checks": errata_checks,
        "bill_match": bill_match,
        "bill_error": bill_error,
        "entries_total": len(entries),
        "entry_categories": category_counts,
        "entry_types": type_counts,
        "entry_failures": entry_failures,
        "entry_case_match": entry_match,
        "evidence_paths_missing": evidence_missing,
    },
    "failure_count": len(failures),
    "failures": failures,
    "result": "PASS" if not failures else "FAIL",
    "limits": [
        "12 条用例中只有 C4-BILL-01 与 C4-ENTRY-01 由本机纯函数实跑；其余 10 条引用 A 的 426 项全量证据。",
        "本机未安装 Streamlit，页面测试未重跑；页面部分引用 A 的 R1/R2 与 A4 证据。",
        "Windows 沙箱 ACL 使临时目录清理失败，数据库类测试未在本机重跑；不冒充 C 自测通过。",
        "route() 结果只代表展示草稿，不代表已授权、资源有效或业务执行。",
    ],
}

(PKG / "selfcheck.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["result"] == "PASS" else 1)
