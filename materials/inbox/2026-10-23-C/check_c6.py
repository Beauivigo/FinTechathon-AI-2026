# -*- coding: utf-8 -*-
"""C 第六轮最后交接自查：C5 原包复跑 + 问题清单 + 路由边界。

在仓库根目录运行：
    py -X utf8 materials/inbox/2026-10-21-C6/check_c6.py

可用 C6_TMP 指定临时父目录；不设置时使用系统临时目录。
"""
import io
import json
import os
import runpy
import shutil
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from uuid import uuid4

PKG = Path(__file__).resolve().parent
C5 = PKG / "C5原包"


def find_repo_root(start):
    for candidate in [start, *start.parents]:
        if (candidate / "bank/gateway.py").is_file() and (candidate / "shared/contracts.py").is_file():
            return candidate
    env = os.environ.get("C6_REPO_ROOT", "").strip()
    if env and (Path(env) / "bank/gateway.py").is_file():
        return Path(env)
    return None


ROOT = find_repo_root(PKG) or find_repo_root(Path.cwd())
if ROOT is None:
    print("无法定位仓库根目录：请把本包放回 materials/inbox/2026-10-21-C6，"
          "或在仓库根目录运行，或设置 C6_REPO_ROOT。")
    raise SystemExit(2)
sys.path.insert(0, str(ROOT))

from agent.routing import route  # noqa: E402

failures = []


def fail(scope, message):
    failures.append({"scope": scope, "message": message})


def sha256(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- C5 manifest
c5_manifest = json.loads((C5 / "manifest.json").read_text(encoding="utf-8"))
c5_bad = [item["file"] for item in c5_manifest["files"] if sha256(C5 / item["file"]) != item["sha256"]]
if len(c5_manifest["files"]) != 10:
    fail("c5_manifest", f"文件数应为 10，实际 {len(c5_manifest['files'])}")
if c5_bad:
    fail("c5_manifest", f"哈希不匹配: {c5_bad}")

c5_self = json.loads((C5 / "selfcheck.json").read_text(encoding="utf-8"))
if c5_self.get("result") != "PASS" or c5_self.get("failure_count") != 0:
    fail("c5_selfcheck", "C5 原包 selfcheck 不是 PASS/0")

# ---------------------------------------------------------------- rerun C5 in temp copy
base = Path(os.environ["C6_TMP"]) if os.environ.get("C6_TMP") else Path(tempfile.mkdtemp(prefix="c6_"))
base.mkdir(parents=True, exist_ok=True)
work = base / f"c5copy_{uuid4().hex}"
shutil.copytree(C5, work)
c5_rerun = {"result": "FAIL", "failure_count": None, "group3": 0, "entry": 0,
            "group3_match": False, "entry_match": False, "error": None}
old_env = {key: os.environ.get(key) for key in ("C5_REPO_ROOT", "C5_TMP")}
os.environ["C5_REPO_ROOT"] = str(ROOT)
os.environ["C5_TMP"] = str(base)
try:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            runpy.run_path(str(work / "check_c5.py"), run_name="__main__")
        except SystemExit as exc:
            if int(exc.code or 0) != 0:
                raise RuntimeError(f"check_c5.py exited {exc.code}") from exc
    rerun_self = json.loads((work / "selfcheck.json").read_text(encoding="utf-8"))
    c5_rerun = {
        "result": rerun_self.get("result"),
        "failure_count": rerun_self.get("failure_count"),
        "group3": len(rerun_self.get("group3", [])),
        "entry": len(rerun_self.get("entry", [])),
        "group3_match": all(item.get("match") for item in rerun_self.get("group3", [])),
        "entry_match": all(item.get("match") for item in rerun_self.get("entry", [])),
        "error": None,
    }
except Exception as exc:  # noqa: BLE001
    c5_rerun["error"] = f"{type(exc).__name__}: {exc}"
    fail("c5_rerun", c5_rerun["error"])
finally:
    for key, value in old_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    shutil.rmtree(work, ignore_errors=True)

if c5_rerun["result"] != "PASS" or c5_rerun["failure_count"] != 0:
    fail("c5_rerun", f"C5 复跑不是 PASS/0: {c5_rerun}")
if c5_rerun["group3"] != 12 or not c5_rerun["group3_match"]:
    fail("c5_rerun", f"C5 group3 应为 12/12: {c5_rerun}")
if c5_rerun["entry"] != 6 or not c5_rerun["entry_match"]:
    fail("c5_rerun", f"C5 entry 应为 6/6: {c5_rerun}")

# ---------------------------------------------------------------- C6 route edges
edge_texts = [
    "从账户 demo_account_a 赎回理财 -100份",
    "从账户 demo_account_a 赎回理财 0份",
    "从账户 demo_account_a 赎回理财 100.12345份",
    "从账户 demo_account_a 赎回理财 100份 买理财 WP-C02 100元",
]
c6_routes = []
for text in edge_texts:
    result = route(text)
    c6_routes.append({"text": text, "status": result.get("status"), "intent": result.get("intent")})
    if result.get("status") == "ready":
        fail("c6_route", f"边界输入不应 ready: {text} -> {result}")

# ---------------------------------------------------------------- problem list
problem_file = PKG / "02_第六轮问题清单.json"
problems = json.loads(problem_file.read_text(encoding="utf-8"))
cases = problems.get("cases", [])
required = ["id", "title", "source", "precondition", "input", "expected", "actual", "repro", "version", "status", "evidence"]
allowed = {"无新增", "已修复", "仍存在", "待核实", "未执行"}
ids = [item.get("id") for item in cases]
if len(cases) != 8:
    fail("problem_list", f"问题行应为 8，实际 {len(cases)}")
if len(set(ids)) != len(ids):
    fail("problem_list", f"问题 id 重复: {ids}")
for item in cases:
    missing = [key for key in required if key not in item or item[key] in (None, "", [], {})]
    if missing:
        fail(item.get("id", "?"), f"缺少字段: {missing}")
    if item.get("status") not in allowed:
        fail(item.get("id", "?"), f"status 非法: {item.get('status')}")
if not any(item.get("status") == "无新增" for item in cases):
    fail("problem_list", "至少需要 C6-P01/C6-P02 的“无新增”结论")
if problems.get("summary", {}).get("new_issues") != 0:
    fail("problem_list", "summary.new_issues 应为 0")

# ---------------------------------------------------------------- C6 manifest
manifest_path = PKG / "manifest.json"
c6_manifest = {"files": 0, "bad": []}
if manifest_path.exists():
    declared = json.loads(manifest_path.read_text(encoding="utf-8"))
    c6_manifest["files"] = len(declared.get("files", []))
    for item in declared.get("files", []):
        path = PKG / item["file"]
        if not path.is_file() or sha256(path) != item["sha256"]:
            c6_manifest["bad"].append(item["file"])
    if c6_manifest["bad"]:
        fail("c6_manifest", f"哈希不匹配: {c6_manifest['bad']}")

# ---------------------------------------------------------------- summary
summary = {
    "package": "C 第六轮最后交接（C6-1.0）",
    "member": "C",
    "round": "第六轮（2026-10-21 最后材料；2026-10-22 提交）",
    "baseline": "main@02fe746",
    "c5_baseline": "main@a505d20",
    "python": sys.version,
    "c5_manifest": {"files": len(c5_manifest["files"]), "bad": c5_bad},
    "c5_rerun": c5_rerun,
    "c6_manifest": c6_manifest,
    "c5_routes": c6_routes,
    "problem_list": {
        "rows": len(cases),
        "ids": ids,
        "statuses": {item.get("id"): item.get("status") for item in cases},
    },
    "failure_count": len(failures),
    "failures": failures,
    "result": "PASS" if not failures else "FAIL",
    "limits": [
        "C6 只复跑 C5 组3/入口并做路由边界抽查；页面/恢复/审计未由 C 重跑。",
        "A-R08、A-R01/A-R02、G12/G14/G15、G13 等仍按 C5 标注待核实/未执行，未填 PASS。",
        "C5 原包按哈希校验；C6 不修改共享契约、代码或日常库。",
    ],
}
(PKG / "selfcheck.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
raise SystemExit(0 if summary["result"] == "PASS" else 1)
