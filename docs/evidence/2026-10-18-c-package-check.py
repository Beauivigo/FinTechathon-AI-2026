"""C 本轮增量包复算核对（只读；不导入运行库）。

对《03_C模拟新增流水.csv》按 C 的 R01/R03 规则独立复算，
与《06_规则算例复核日志.txt》的 10 条预期逐条对比，结果写入 docs/evidence。
"""
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT.parent / "群组资料" / "C" / "03_C模拟新增流水.csv"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "evidence" / "2026-10-18-c-package-check.json"


def cents(text):
    return int(round(float(text) * 100))


def load():
    rows = []
    with SRC.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append({
                "id": row["模拟流水号"],
                "date": date.fromisoformat(row["日期"]),
                "kind": row["kind"],
                "direction": "debit" if row["方向"] == "支出" else "credit",
                "amount": cents(row["金额（元）"]),
                "account": row["归并账户"],
                "payee": row["收款人编号"],
                "link": row["原交易关联编号"],
                "group": row["用途分组"],
            })
    return rows


def r01(rows):
    cases = {}
    for row in rows:
        if not row["link"]:
            continue
        bucket = cases.setdefault(row["link"], {"debit": 0, "credit": 0, "rows": []})
        bucket[row["direction"]] += row["amount"]
        bucket["rows"].append(row)
    results = []
    for link, bucket in sorted(cases.items()):
        debit_rows = [r for r in bucket["rows"] if r["direction"] == "debit"]
        credit_rows = [r for r in bucket["rows"] if r["direction"] == "credit"]
        assert len(debit_rows) == 1, link
        start = debit_rows[0]["date"]
        end = start + timedelta(days=90)
        in_window = [r for r in credit_rows if start <= r["date"] <= end]
        out_window = [r for r in credit_rows if r["date"] > end]
        delta = sum(r["amount"] for r in in_window) - bucket["debit"]
        if out_window and not in_window:
            verdict = "UNJUDGEABLE"
        elif delta != 0:
            verdict = "HIT"
        else:
            verdict = "MISS"
        results.append({"link": link, "debit": bucket["debit"],
                        "credit_in_window": sum(r["amount"] for r in in_window),
                        "delta": delta, "window": [start.isoformat(), end.isoformat()],
                        "verdict": verdict})
    return results


def r03(rows):
    results = []
    for row in rows:
        if row["direction"] != "debit" or row["kind"] not in {"consumption", "subscription", "transfer", "wealth"}:
            continue
        others = [r for r in rows
                  if r["account"] == row["account"] and r["direction"] == "debit"
                  and r["kind"] in {"consumption", "subscription", "transfer", "wealth"} and r["id"] != row["id"]]
        start = min(r["date"] for r in others + [row])
        end = max(r["date"] for r in others + [row])
        days = (end - start).days + 1
        base = sum(r["amount"] for r in others if start <= r["date"] <= end)
        hit_a = row["amount"] >= 500000
        hit_b = base > 0 and row["amount"] >= (base / days) * 10
        results.append({"id": row["id"], "amount": row["amount"],
                        "window": [start.isoformat(), end.isoformat()], "days": days,
                        "baseline_minor": round(base / days, 2),
                        "hit_a": hit_a, "hit_b": hit_b})
    return results


def main():
    rows = load()
    report = {
        "source": str(SRC),
        "rows": len(rows),
        "r01": r01(rows),
        "r03": r03(rows),
        "checks": [],
    }
    expected = {
        "TR-SIM-01": "HIT", "TR-SIM-02": "MISS", "TR-SIM-03": "HIT",
        "TR-SIM-04": "MISS", "TR-SIM-05": "UNJUDGEABLE",
    }
    for item in report["r01"]:
        report["checks"].append({"case": item["link"], "expected": expected[item["link"]],
                                 "actual": item["verdict"], "ok": expected[item["link"]] == item["verdict"]})
    expected_r03 = {
        "SIM-C-011": (True, False), "SIM-C-012": (False, False), "SIM-C-013": (True, False),
        "SIM-C-019": (False, True), "SIM-C-025": (False, False),
    }
    by_id = {item["id"]: item for item in report["r03"]}
    for key, (a, b) in expected_r03.items():
        item = by_id[key]
        report["checks"].append({"case": key, "expected": [a, b],
                                 "actual": [item["hit_a"], item["hit_b"]],
                                 "ok": (item["hit_a"], item["hit_b"]) == (a, b)})
    report["pass"] = sum(1 for c in report["checks"] if c["ok"])
    report["fail"] = sum(1 for c in report["checks"] if not c["ok"])
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rows": report["rows"], "pass": report["pass"], "fail": report["fail"]}, ensure_ascii=False))
    for check in report["checks"]:
        print(("PASS" if check["ok"] else "FAIL"), check["case"], check["actual"])


if __name__ == "__main__":
    main()
