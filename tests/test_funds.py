"""P2 内部资金测试：可信调用替身代行未来授权网关，不代表用户授权已实现。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from bank.gateway import BankGateway
from bank.seed import initialize, reset_demo, demo_path
from bank.services import SandboxServices
from bank.store import Store, STORAGE_VERSION
from bank.transfer import handle
from shared import ServiceError, adapt_request, adapt_result, operation_digest, validate_result
from shared.contracts import MAX_SAFE_INTEGER, ContractError


class FundsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "test.sqlite3")
        initialize(self.store)
        self.services = SandboxServices(self.store, clock=lambda: datetime(2026, 10, 13, 4, tzinfo=timezone.utc))
        self.gateway = BankGateway(self.store)

    def request(self, amount=20000, key="transfer_test", **params):
        return adapt_request("transfer.create", dict(account_id="demo_account_a", payee_id="demo_payee_li",
                             amount_minor=amount) | params, request_id="req_"+uuid4().hex,
                             trace_id="trace_test", idempotency_key=key)

    def execute_internal(self, request=None, *, principal="demo_user_a"):
        request = request or self.request()
        with self.store.connection(write=True) as connection:
            with self.services.transfer_context(connection, request, principal_id=principal) as context:
                raw = handle(request["params"], context=context)
        return validate_result(request, adapt_result(request, raw, source="sandbox"))

    def balances(self):
        with self.store.connection() as connection:
            return {row["account_id"]: (row["balance_minor"], row["available_minor"], row["reserved_minor"])
                    for row in connection.execute("SELECT * FROM accounts")}

    def counts(self):
        with self.store.connection() as connection:
            return {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                    for name in ("transfers", "ledger_entries", "fund_postings", "operations", "audit_events")}

    def assert_clean(self):
        self.assertEqual(self.balances()["demo_account_a"], (200000, 200000, 0))
        self.assertEqual(self.balances()["demo_account_li"], (0, 0, 0))
        self.assertEqual(self.counts(), dict(transfers=0, ledger_entries=0, fund_postings=0, operations=0, audit_events=0))

    def test_transfer_commits_both_sides_receipts_result_and_audit(self):
        request = self.request()
        result = self.execute_internal(request)
        self.assertEqual(self.balances()["demo_account_a"], (180000, 180000, 0))
        self.assertEqual(self.balances()["demo_account_li"], (20000, 20000, 0))
        self.assertEqual(sum(x[0] for x in self.balances().values()), 200000)
        self.assertEqual(self.counts(), dict(transfers=1, ledger_entries=2, fund_postings=2, operations=1, audit_events=1))
        with self.store.connection() as c:
            rows = c.execute("SELECT * FROM ledger_entries ORDER BY direction").fetchall()
            self.assertEqual({x["reference_id"] for x in rows}, {result["data"]["transaction_id"]})
            self.assertEqual({x["direction"] for x in rows}, {"debit", "credit"})
            self.assertEqual({x["amount_minor"] for x in rows}, {20000})
            self.assertNotEqual(rows[0]["posting_key"], rows[1]["posting_key"])
            for account in c.execute("SELECT * FROM accounts"):
                delta = c.execute("SELECT COALESCE(SUM(CASE direction WHEN 'credit' THEN amount_minor ELSE -amount_minor END),0) FROM ledger_entries WHERE account_id=?", (account["account_id"],)).fetchone()[0]
                self.assertEqual(account["balance_minor"], account["opening_balance_minor"] + delta)
        query = adapt_request("operation.get", dict(original_action="transfer.create", original_idempotency_key="transfer_test"),
                              request_id="query", trace_id="trace_query")
        saved = self.gateway.execute(query, principal_id="demo_user_a")
        self.assertEqual(saved["data"], {"operation_request":request, "operation_result":result})

    def test_no_success_is_visible_before_outer_commit(self):
        request = self.request()
        with self.store.connection(write=True) as c:
            with self.services.transfer_context(c, request, principal_id="demo_user_a") as context:
                raw = handle(request["params"], context=context)
                self.assertEqual(raw["status"], "succeeded")  # 内部返回，不是外部已提交证明。
                self.assert_clean()  # 另一连接只看见提交前状态。
        self.assertEqual(self.balances()["demo_account_a"][0], 180000)

    def test_same_operation_retries_and_restart_do_not_post_twice(self):
        request = self.request()
        first = self.execute_internal(request)
        self.services = SandboxServices(Store(self.store.path))
        self.store = self.services.store
        retry = self.request()
        retry["trace_id"] = "another_trace"
        second = self.execute_internal(retry)
        self.assertEqual(first["data"], second["data"])
        self.assertEqual(second["request_id"], retry["request_id"])
        self.assertEqual(second["trace_id"], "another_trace")
        self.assertEqual(self.counts()["ledger_entries"], 2)
        with self.store.connection() as c:
            self.assertEqual(json.loads(c.execute("SELECT request_json FROM operations").fetchone()[0]), request)

    def test_same_key_changed_amount_payee_or_memo_conflicts(self):
        self.execute_internal()
        for request in (self.request(20001), self.request(payee_id="demo_payee_wang_1"), self.request(memo="changed")):
            with self.subTest(request=request), self.assertRaises(ServiceError) as caught:
                self.execute_internal(request)
            self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.counts()["transfers"], 1)

    def test_concurrent_same_key_is_one_transfer(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.execute_internal(self.request()), range(2)))
        self.assertEqual(results[0]["data"], results[1]["data"])
        self.assertEqual(self.counts()["transfers"], 1)

    def test_insufficient_and_concurrent_balance_never_overdraw(self):
        with self.assertRaises(ServiceError) as caught:
            self.execute_internal(self.request(200001))
        self.assertEqual(caught.exception.code, "INSUFFICIENT_FUNDS")
        self.assert_clean()
        def attempt(key):
            try:
                return self.execute_internal(self.request(150000, key=key))["status"]
            except ServiceError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("a", "b")))
        self.assertCountEqual(results, ["succeeded", "INSUFFICIENT_FUNDS"])
        self.assertEqual(self.balances()["demo_account_a"][0], 50000)
        self.assertEqual(self.counts()["transfers"], 1)

    def test_reserved_funds_cannot_be_spent_by_normal_debit(self):
        with self.store.connection(write=True) as c:
            c.execute("UPDATE accounts SET available_minor=10000,reserved_minor=190000 WHERE account_id='demo_account_a'")
        with self.assertRaises(ServiceError) as caught:
            self.execute_internal()
        self.assertEqual(caught.exception.code, "INSUFFICIENT_FUNDS")
        self.execute_internal(self.request(10000))
        self.assertEqual(self.balances()["demo_account_a"], (190000, 0, 190000))

    def test_wrong_owner_untrusted_payee_and_self_transfer_are_rejected(self):
        cases = [(self.request(), "demo_user_li"), (self.request(payee_id="unknown"), "demo_user_a"),
                 (self.request(account_id="demo_account_li"), "demo_user_a")]
        for request, principal in cases:
            with self.assertRaises(ServiceError):
                self.execute_internal(request, principal=principal)
        with self.store.connection(write=True) as c:
            c.execute("UPDATE payees SET account_id='demo_account_a' WHERE payee_id='demo_payee_li'")
        with self.assertRaises(ServiceError) as caught:
            self.execute_internal()
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")
        self.assert_clean()

    def test_fake_read_foreign_and_expired_transactions_cannot_post(self):
        request = self.request()
        with self.store.connection() as c:
            with self.assertRaises(ServiceError):
                with self.services.transfer_context(c, request, principal_id="demo_user_a"):
                    self.fail("read connection accepted")
        foreign = Store(self.store.path)
        with foreign.connection(write=True) as c:
            with self.assertRaises(ServiceError):
                with self.services.transfer_context(c, request, principal_id="demo_user_a"):
                    self.fail("foreign connection accepted")
        with self.assertRaises(ServiceError):
            self.services.post_funds({}, {}, principal_id="demo_user_a")
        with self.store.connection(write=True) as c:
            with self.services.transfer_context(c, request, principal_id="demo_user_a") as context:
                token = context["transaction"]
                handle(request["params"], context=context)
        with self.assertRaises(ServiceError):
            self.services.post_funds(token, {}, principal_id="demo_user_a")

    def test_credit_first_arbitrary_credit_and_unfinished_debit_roll_back(self):
        request = self.request()
        with self.store.connection(write=True) as c:
            with self.assertRaises(ServiceError):
                with self.services.transfer_context(c, request, principal_id="demo_user_a") as context:
                    state = self.services._transfer_state(context["transaction"], "demo_user_a")
                    with self.assertRaises(ServiceError):
                        self.services.post_funds(context["transaction"], state["commands"]["credit"], principal_id="demo_user_a")
                    changed = state["commands"]["debit"] | {"account_id":"demo_account_li"}
                    with self.assertRaises(ServiceError):
                        self.services.post_funds(context["transaction"], changed, principal_id="demo_user_a")
                    self.services.post_funds(context["transaction"], state["commands"]["debit"], principal_id="demo_user_a")
                    # 没有完成业务结果，作用域退出须撤销扣款；外层捕获后仍不能提交半笔。
        self.assert_clean()

    def test_posting_replay_checks_all_fields_and_does_not_change_balance(self):
        request = self.request()
        with self.store.connection(write=True) as c:
            with self.services.transfer_context(c, request, principal_id="demo_user_a") as context:
                state = self.services._transfer_state(context["transaction"], "demo_user_a")
                command = deepcopy(state["commands"]["debit"])
                first = self.services.post_funds(context["transaction"], command, principal_id="demo_user_a")
                self.assertEqual(first, self.services.post_funds(context["transaction"], command, principal_id="demo_user_a"))
                for change in ({"amount_minor":20001}, {"account_id":"demo_account_li"}, {"reference_id":"different"},
                               {"trace_id":"different"}, {"effect":"credit"}, {"kind":"wealth"}):
                    with self.assertRaises(ServiceError) as caught:
                        self.services.post_funds(context["transaction"], command | change, principal_id="demo_user_a")
                    self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")
                handle(request["params"], context=context)
        self.assertEqual(self.balances()["demo_account_a"][0], 180000)
        self.assertEqual(self.counts()["fund_postings"], 2)

    def test_posting_error_caught_by_caller_cannot_leave_extra_debit(self):
        request = self.request()
        with self.store.connection(write=True) as c:
            with self.services.transfer_context(c, request, principal_id="demo_user_a") as context:
                command = self.services._transfer_state(context["transaction"], "demo_user_a")["commands"]["debit"]
                with patch("bank.services.validate_funds_receipt", side_effect=ContractError("fault")):
                    with self.assertRaises(ContractError):
                        self.services.post_funds(context["transaction"], command, principal_id="demo_user_a")
                handle(request["params"], context=context)
        self.assertEqual(self.balances()["demo_account_a"][0], 180000)
        self.assertEqual(self.counts()["ledger_entries"], 2)

    def test_fault_after_each_leg_rolls_back_all_records(self):
        actual = self.services.post_funds
        for fault_effect in ("debit", "credit"):
            def inject(transaction, command, *, principal_id):
                receipt = actual(transaction, command, principal_id=principal_id)
                if command["effect"] == fault_effect:
                    raise RuntimeError("injected after posting")
                return receipt
            with patch.object(self.services, "post_funds", side_effect=inject):
                with self.assertRaises(RuntimeError):
                    self.execute_internal()
            self.assert_clean()

    def test_fault_before_result_and_during_result_or_audit_save_rolls_back(self):
        with patch("bank.transfer.adapt_result", side_effect=ContractError("bad result")):
            with self.assertRaises(ContractError):
                self.execute_internal()
        self.assert_clean()
        for table in ("operations", "audit_events"):
            with self.store.connection(write=True) as c:
                c.execute(f"CREATE TRIGGER fail_save BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'injected'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                self.execute_internal()
            self.assert_clean()
            with self.store.connection(write=True) as c:
                c.execute("DROP TRIGGER fail_save")

    def test_outer_commit_failure_rolls_back_all_transfer_records(self):
        with self.store.connection(write=True) as c:
            c.execute("CREATE TABLE deferred_check (principal_id TEXT REFERENCES principals DEFERRABLE INITIALLY DEFERRED)")
        request = self.request()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connection(write=True) as c:
                with self.services.transfer_context(c, request, principal_id="demo_user_a") as context:
                    handle(request["params"], context=context)
                c.execute("INSERT INTO deferred_check VALUES ('missing')")
        self.assert_clean()

    def test_malformed_receipt_context_and_float_amount_do_not_commit(self):
        actual = self.services.post_funds
        def corrupt(transaction, command, *, principal_id):
            return actual(transaction, command, principal_id=principal_id) | {"amount_minor":1}
        with patch.object(self.services, "post_funds", side_effect=corrupt):
            with self.assertRaises(ContractError):
                self.execute_internal()
        for change in ({"amount_minor":20000.0}, {"amount_minor":True}, {"payee_id":"demo_payee_wang_1"}):
            request = self.request()
            with self.assertRaises(ServiceError):
                with self.store.connection(write=True) as c:
                    with self.services.transfer_context(c, request, principal_id="demo_user_a") as context:
                        handle(request["params"] | change, context=context)
        with self.assertRaises(ServiceError):
            handle({}, context={})
        self.assert_clean()

    def test_credit_overflow_and_daily_safe_integer_limit_roll_back(self):
        with self.store.connection(write=True) as c:
            c.execute("UPDATE accounts SET opening_balance_minor=?,balance_minor=?,available_minor=? WHERE account_id='demo_account_li'",
                      (MAX_SAFE_INTEGER,)*3)
        with self.assertRaises(ServiceError) as caught:
            self.execute_internal()
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        self.assertEqual(self.balances()["demo_account_a"][0], 200000)
        self.assertEqual(self.counts()["ledger_entries"], 0)
        with self.store.connection(write=True) as c:
            c.execute("UPDATE accounts SET opening_balance_minor=0,balance_minor=0,available_minor=0 WHERE account_id='demo_account_li'")
            c.execute("INSERT INTO transfers VALUES ('fixture_max','demo_user_a','demo_account_a','demo_payee_li',?,'2026-10-13T00:00:00.000000Z')", (MAX_SAFE_INTEGER,))
        with self.assertRaises(ServiceError) as caught:
            self.execute_internal(self.request(1))
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        self.assertEqual(self.counts()["transfers"], 1)
        self.assertEqual(self.counts()["ledger_entries"], 0)

    def test_pending_record_is_updated_but_unknown_or_terminal_failure_is_not_reexecuted(self):
        request = self.request()
        digest = operation_digest(request, principal_id="demo_user_a")
        pending = adapt_result(request, {"status":"awaiting_confirmation", "next_action":dict(
            challenge_id="fixture_challenge", kind="confirm", operation_digest=digest, expires_at="2099-01-01T00:00:00Z")}, source="gateway")
        with self.store.connection(write=True) as c:
            c.execute("INSERT INTO operations VALUES (?,?,?,?,?,?)", ("demo_user_a","transfer.create","transfer_test",digest,json.dumps(request),json.dumps(pending)))
        self.execute_internal(request)  # 此调用代行可信网关，P3 才核验挑战本身。
        self.assertEqual(self.counts()["operations"], 1)
        for status, code in (("unknown","RESULT_UNKNOWN"),("failed","INSUFFICIENT_FUNDS")):
            other = self.request(key=status)
            result = adapt_result(other, {"status":status, "error":{"code":code,"message":"fixture"}}, source="sandbox")
            with self.store.connection(write=True) as c:
                c.execute("INSERT INTO operations VALUES (?,?,?,?,?,?)", ("demo_user_a","transfer.create",status,
                    operation_digest(other, principal_id="demo_user_a"),json.dumps(other),json.dumps(result)))
            self.assertEqual(self.execute_internal(other)["status"], status)
        self.assertEqual(self.counts()["transfers"], 1)

    def test_budget_actions_unavailable_and_public_gateway_requires_authorization(self):
        request = self.request()
        with self.assertRaises(ServiceError):
            with self.store.connection(write=True) as c:
                with self.services.transfer_context(c, request, principal_id="demo_user_a") as context:
                    cmd = self.services._transfer_state(context["transaction"], "demo_user_a")["commands"]["debit"]
                    self.services.post_funds(context["transaction"], cmd | {"effect":"reserve","reservation_id":"res_test"}, principal_id="demo_user_a")
        self.assert_clean()
        self.assertEqual(self.gateway.execute(request, principal_id="demo_user_a")["status"], "awaiting_confirmation")
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0], 0)

    def test_p1_schema_migration_preserves_existing_data_and_is_repeatable(self):
        with self.store.connection(write=True) as c:
            c.execute("DROP TABLE fund_postings")
            c.execute("PRAGMA user_version=1")
            c.execute("UPDATE accounts SET balance_minor=12300,available_minor=12300 WHERE account_id='demo_account_a'")
        self.assertFalse(initialize(self.store))
        self.assertFalse(initialize(self.store))
        self.assertEqual(self.balances()["demo_account_a"][0], 12300)
        with self.store.connection() as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], STORAGE_VERSION)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM fund_postings").fetchone()[0], 0)

    def test_reset_clears_postings_with_financial_records(self):
        with patch("bank.seed.PROJECT_ROOT", self.root):
            self.store = Store(demo_path())
            initialize(self.store)
            self.services = SandboxServices(self.store)
            self.execute_internal()
            reset_demo(confirm=True)
            self.assert_clean()

    def test_failed_or_incomplete_p1_migration_preserves_old_version_and_data(self):
        with self.store.connection(write=True) as c:
            c.execute("UPDATE accounts SET balance_minor=12300,available_minor=12300 WHERE account_id='demo_account_a'")
            c.execute("PRAGMA user_version=1")  # 故意留下新表，制造升级冲突。
        with self.assertRaises(sqlite3.OperationalError):
            initialize(self.store)
        with closing(self.store._connect()) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0], 12300)
            c.execute("DROP TABLE fund_postings")
            c.execute("DROP TABLE page_cursors")
        with self.assertRaises(ServiceError):
            initialize(self.store)
        with closing(self.store._connect()) as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0], 12300)


if __name__ == "__main__":
    unittest.main()
