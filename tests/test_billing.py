"""B1 核心验收；独立模拟样例，不替代 C 的独立验收或 B2 权限测试。"""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from billing import analyze
from shared.adapters import adapt_request, adapt_result
from shared.contracts import MAX_SAFE_INTEGER, validate_result
from shared.services import ServiceError


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parent / "fixtures/billing_b0.json").read_text(encoding="utf-8"))
        self.rows = self.fixture["transactions"]
        self.params = dict(account_id="b0_account_a", start_date="2026-08-01", end_date="2026-08-31")

    def assert_code(self, code, params, rows):
        with self.assertRaises(ServiceError) as caught:
            analyze(params, rows)
        self.assertEqual(caught.exception.code, code)

    def test_all_hand_answers_and_public_result_contract(self):
        for case in self.fixture["cases"]:
            with self.subTest(case=case["case_id"]):
                data = analyze(case["params"], self.rows)
                self.assertEqual(data["total_expense_minor"], case["total_expense_minor"])
                self.assertEqual(data["categories"], sorted(case["categories"], key=lambda r: r["category"]))
                refund = case["refund_minor"]
                self.assertIn(f"本期退款入账 {refund // 100}.{refund % 100:02d} 元", data["report"])
                self.assertEqual(data["anomalies"], [])
                self.assertIn("自动异常检测尚未启用，空列表不代表没有异常", data["report"])
                request = adapt_request("bill.analyze", case["params"], request_id="b1", trace_id="b1")
                result = adapt_result(request, dict(status="succeeded", data=data), source="sandbox")
                validate_result(request, result)

    def test_readonly_order_independent_and_repeatable(self):
        before = deepcopy((self.params, self.rows))
        result = analyze(self.params, self.rows)
        self.assertEqual(result, analyze(self.params, reversed(self.rows)))
        result["categories"].clear()
        self.assertEqual(analyze(self.params, self.rows)["total_expense_minor"], 12700)
        self.assertEqual((self.params, self.rows), before)

    def test_more_than_one_hundred_rows(self):
        rows = [dict(self.rows[0], transaction_id=f"many_{i}", amount_minor=1) for i in range(251)]
        self.assertEqual(analyze(self.params, iter(rows))["total_expense_minor"], 251)

    def test_shanghai_same_day_closed_boundaries(self):
        times = ["2026-07-31T15:59:59.999999Z", "2026-07-31T16:00:00Z",
                 "2026-08-01T15:59:59.999999Z", "2026-08-01T16:00:00Z"]
        rows = [dict(self.rows[0], transaction_id=f"edge_{i}", amount_minor=2**i, occurred_at=t)
                for i, t in enumerate(times)]
        params = dict(self.params, end_date="2026-08-01")
        self.assertEqual(analyze(params, rows)["total_expense_minor"], 6)

    def test_year_fee_not_amortized_and_category_not_inferred(self):
        row = dict(self.rows[1], amount_minor=120000, category="原分类", merchant_name="餐饮商户")
        data = analyze(self.params, [row])
        self.assertEqual(data["categories"], [dict(category="原分类", amount_minor=120000)])

    def test_direction_matters(self):
        rows = [dict(self.rows[0], direction="credit"), dict(self.rows[1], direction="credit"),
                dict(self.rows[3], direction="debit")]
        data = analyze(self.params, rows)
        self.assertEqual(data["total_expense_minor"], 0)
        self.assertIn("本期退款入账 0.00 元", data["report"])

    def test_invalid_request(self):
        for changes in [dict(start_date="2026-02-30"), dict(start_date="2026-09-01"),
                        dict(start_date="2026-8-01"), dict(extra=True), dict(account_id="")]:
            with self.subTest(changes=changes):
                self.assert_code("INVALID_REQUEST", dict(self.params, **changes), [])

    def test_bad_records_fail_without_guessing(self):
        variants = [dict(self.rows[0], amount_minor=x) for x in (True, 1.5, 0, -1, MAX_SAFE_INTEGER + 1)]
        variants += [dict(self.rows[0], occurred_at="2026-08-01T00:00:00"),
                     dict(self.rows[0], category=""), dict(self.rows[0], extra=True),
                     {k: v for k, v in self.rows[0].items() if k != "category"}]
        for row in variants:
            with self.subTest(row=row):
                self.assert_code("UPSTREAM_ERROR", self.params, [row])
        self.assert_code("UPSTREAM_ERROR", self.params, None)

    def test_duplicate_transaction_fails(self):
        self.assert_code("UPSTREAM_ERROR", self.params, [self.rows[0], deepcopy(self.rows[0])])

    def test_total_overflow_across_categories(self):
        rows = [dict(self.rows[0], amount_minor=MAX_SAFE_INTEGER), self.rows[1]]
        self.assert_code("LIMIT_EXCEEDED", self.params, rows)

    def test_refund_overflow(self):
        rows = [dict(self.rows[3], amount_minor=MAX_SAFE_INTEGER),
                dict(self.rows[3], transaction_id="another_refund")]
        self.assert_code("LIMIT_EXCEEDED", self.params, rows)

    def test_maximum_amount_report_is_exact(self):
        data = analyze(self.params, [dict(self.rows[0], amount_minor=MAX_SAFE_INTEGER)])
        self.assertIn("90071992547409.91 元", data["report"])
        self.assertEqual(data["total_expense_minor"], MAX_SAFE_INTEGER)

    def test_category_capacity_no_truncation(self):
        rows = [dict(self.rows[0], transaction_id=f"cat_{i}", category=f"分类{i}") for i in range(101)]
        self.assertEqual(len(analyze(self.params, rows[:100])["categories"]), 100)
        self.assert_code("LIMIT_EXCEEDED", self.params, rows)

    def test_output_byte_capacity_no_truncation(self):
        rows = [dict(self.rows[0], transaction_id=f"long_{i}", category="长" * 4000 + str(i)) for i in range(100)]
        self.assert_code("LIMIT_EXCEEDED", self.params, rows)


if __name__ == "__main__":
    unittest.main()
