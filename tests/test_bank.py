"""P1 持久化/归属/分页验收；流水和原操作仅由测试夹具准备，不冒充已实现扣款。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from bank.gateway import BankGateway
from bank.seed import initialize, reset_demo, demo_path
from bank.store import Store, utc_timestamp
from shared import (ServiceError, adapt_request, adapt_result, operation_digest,
                    validate_result, result_text)


class BankTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "sandbox.sqlite3")
        initialize(self.store)
        self.gateway = BankGateway(self.store)

    def request(self, action, params, *, key=None):
        return adapt_request(action, params, request_id="req_" + uuid4().hex,
                             trace_id="trace_test", idempotency_key=key)

    def query(self, action, params, *, principal="demo_user_a"):
        request = self.request(action, params)
        return validate_result(request, self.gateway.execute(request, principal_id=principal))

    def balance(self, account="demo_account_a", principal="demo_user_a"):
        return self.query("account.balance", {"account_id": account}, principal=principal)

    def page(self, **changes):
        params = dict(account_id="demo_account_a", start_date="2026-09-01", end_date="2026-10-31", limit=2)
        params.update(changes)
        return self.query("transaction.list", params)

    def entries(self, rows):
        # 可信历史夹具，不是面向用户的记账接口；每笔同时更新测试账户余额。
        with self.store.connection(write=True) as c:
            for identifier, occurred_at in rows:
                c.execute("""INSERT INTO ledger_entries
                    (transaction_id,account_id,reference_id,posting_key,amount_minor,direction,kind,category,occurred_at)
                    VALUES (?,?,?,?,100,'credit','income','测试收入',?)""",
                    (identifier, "demo_account_a", "fixture_"+identifier, "post_"+identifier, utc_timestamp(occurred_at)))
                c.execute("UPDATE accounts SET balance_minor=balance_minor+100, available_minor=available_minor+100 WHERE account_id='demo_account_a'")

    def operation(self, status="awaiting_confirmation", key="fixture_operation"):
        request = self.request("transfer.create", {"account_id": "demo_account_a", "payee_id": "demo_payee_li",
                                                "amount_minor": 20000}, key=key)
        digest = operation_digest(request, principal_id="demo_user_a")
        if status == "awaiting_confirmation":
            raw = {"status": status, "next_action": {"challenge_id": "fixture_challenge", "kind": "confirm",
                "operation_digest": digest, "expires_at": "2099-01-01T00:00:00Z"}}
            source = "gateway"
        elif status == "succeeded":
            raw = {"status": status, "data": {"transaction_id": "fixture_transfer", "amount_minor": 20000}}
            source = "sandbox"
        else:
            raw = {"status": "failed", "error": {"code": "INSUFFICIENT_FUNDS", "message": "测试余额不足。"}}
            source = "sandbox"
        result = adapt_result(request, raw, source=source)
        with self.store.connection(write=True) as c:
            c.execute("INSERT INTO operations VALUES (?,?,?,?,?,?)", ("demo_user_a", "transfer.create", key,
                      digest, json.dumps(request), json.dumps(result)))
        return request, result

    def test_empty_store_and_queries_do_not_auto_seed(self):
        empty = Store(self.root / "empty.sqlite3")
        empty.initialize()
        gateway = BankGateway(empty)
        request = self.request("account.balance", {"account_id": "demo_account_a"})
        self.assertEqual(gateway.execute(request, principal_id="demo_user_a")["error"]["code"], "FORBIDDEN")
        self.assertTrue(initialize(empty))
        self.assertEqual(gateway.execute(request, principal_id="demo_user_a")["data"]["balance_minor"], 200000)

    def test_missing_database_query_does_not_create_file(self):
        missing = self.root / "missing.sqlite3"
        request = self.request("account.balance", {"account_id": "demo_account_a"})
        result = BankGateway(Store(missing)).execute(request, principal_id="demo_user_a")
        self.assertEqual(result["error"]["code"], "UPSTREAM_ERROR")
        self.assertFalse(missing.exists())

    def test_seed_balances_and_resource_snapshots_match_contract(self):
        context = self.gateway.get_context(principal_id="demo_user_a")
        self.assertEqual(context["accounts"], [{"account_id": "demo_account_a", "display_name": "模拟日常账户"}])
        self.assertEqual({p['product_id'] for p in context['products']}, {'product_1','sample_product_2','sample_product_3'})
        self.assertEqual(len(context['cards']), 3)
        self.assertTrue(all(context[key] == [] for key in ("holdings","questionnaires")))
        self.assertEqual([(i['item_id'],i['price_minor']) for i in context['items']], [('ITEM-BD-FLOWER-01',19900),('ITEM-BD-FLOWER-02',29900),('ITEM-BD-CAKE-01',15800),('ITEM-BD-CAKE-02',22800)])
        self.assertEqual([a['delivery_address_id'] for a in context['addresses']], ['ADDR-BD-01','ADDR-BD-02'])
        self.assertEqual(self.balance()["data"], dict(account_id="demo_account_a", balance_minor=200000,
                         available_minor=200000, reserved_minor=0, currency="CNY"))
        for suffix in ("li", "wang_1", "wang_2"):
            self.assertEqual(self.balance("demo_account_"+suffix, "demo_user_"+suffix)["data"]["balance_minor"], 0)
        self.assertEqual(self.page()["data"], {"transactions": [], "next_cursor": None})

    def test_initialization_is_idempotent_and_separate_process_reads_saved_state(self):
        self.entries([("entry_a", "2026-10-01T09:00:00+08:00")])
        original, result = self.operation()
        self.assertFalse(initialize(self.store))
        self.gateway = BankGateway(Store(self.store.path))
        self.assertEqual(self.balance()["data"]["balance_minor"], 200100)
        saved = self.query("operation.get", {"original_action": "transfer.create", "original_idempotency_key": "fixture_operation"})
        self.assertEqual(saved["data"], {"operation_request": original, "operation_result": result})
        code = "from bank.store import Store; from bank.seed import initialize; import sys; s=Store(sys.argv[1]); assert initialize(s) is False; c=s._connect(); print(c.execute('SELECT balance_minor FROM accounts WHERE account_id=?',('demo_account_a',)).fetchone()[0]); c.close()"
        proc = subprocess.run([sys.executable, "-B", "-c", code, str(self.store.path)],
                              cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "200100")

    def test_concurrent_initialization_imports_seed_only_once(self):
        path = self.root / "concurrent.sqlite3"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: initialize(Store(path)), range(2)))
        self.assertEqual(sorted(results), [False, True])
        with Store(path).connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 4)

    def test_failed_seed_import_rolls_back_and_can_be_retried(self):
        from bank.seed import PAYEES
        empty = Store(self.root / "failed_seed.sqlite3")
        broken = (PAYEES[0][:2] + ("missing_account",) + PAYEES[0][3:],)
        with patch("bank.seed.PAYEES", broken):
            with self.assertRaises(sqlite3.IntegrityError):
                initialize(empty)
        with empty.connection() as c:
            for table in ("principals", "accounts", "payees", "metadata"):
                self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertTrue(initialize(empty))

    def test_unknown_or_other_identity_cannot_read_private_resources(self):
        for identity in ("unknown", "", None, 1):
            with self.subTest(identity=identity):
                self.assertEqual(self.balance(principal=identity)["error"]["code"], "FORBIDDEN")
                with self.assertRaises(ServiceError):
                    self.gateway.get_context(principal_id=identity)
        self.assertEqual(self.balance(principal="demo_user_li")["error"]["code"], "FORBIDDEN")
        params = dict(account_id="demo_account_a", start_date="2026-01-01", end_date="2026-12-31")
        self.assertEqual(self.query("transaction.list", params, principal="demo_user_li")["error"]["code"], "FORBIDDEN")
        context = self.gateway.get_context(principal_id="demo_user_li")
        self.assertEqual([x["account_id"] for x in context["accounts"]], ["demo_account_li"])

    def test_payees_are_scoped_and_ambiguous_names_are_not_guessed(self):
        rows = self.query("payee.resolve", {"query": "小王", "match_type": "name"})["data"]["candidates"]
        self.assertEqual([x["payee_id"] for x in rows], ["demo_payee_wang_1", "demo_payee_wang_2"])
        self.assertTrue(all("account_id" not in row and "phone" not in row for row in rows))
        self.assertEqual(self.query("payee.resolve", {"query": "李明", "match_type": "name"},
                                   principal="demo_user_li")["data"]["candidates"], [])
        rows = self.query("payee.resolve", {"query": "00000000001", "match_type": "phone"})["data"]["candidates"]
        self.assertEqual(rows[0]["payee_id"], "demo_payee_li")
        self.assertEqual(self.query("payee.resolve", {"query": "' OR 1=1 --", "match_type": "name"})["data"]["candidates"], [])

    def test_operation_query_keeps_nested_status_and_hides_other_owners(self):
        for status in ("awaiting_confirmation", "succeeded", "failed"):
            self.operation(status=status, key=status)
            params = {"original_action": "transfer.create", "original_idempotency_key": status}
            result = self.query("operation.get", params)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["data"]["operation_result"]["status"], status)
            if status == "awaiting_confirmation":
                request = self.request("operation.get", params)
                display = result_text(request, self.gateway.execute(request, principal_id="demo_user_a"))
                self.assertIn("待用户确认", display)
                self.assertNotIn("转账已完成", display)
            denied = self.query("operation.get", params, principal="demo_user_li")
            self.assertEqual(denied["error"]["code"], "NOT_FOUND")
            self.assertEqual(denied["data"], {})
        self.assertEqual(self.query("operation.get", {"original_action":"transfer.create",
            "original_idempotency_key":"missing"})["error"]["code"], "NOT_FOUND")

    def test_corrupt_operation_cannot_be_presented_as_success(self):
        self.operation()
        with self.store.connection(write=True) as c:
            c.execute("UPDATE operations SET digest='tampered'")
        result = self.query("operation.get", {"original_action":"transfer.create", "original_idempotency_key":"fixture_operation"})
        self.assertEqual(result["error"]["code"], "UPSTREAM_ERROR")
        self.assertEqual(result["data"], {})

    def test_stable_paging_excludes_new_backdated_rows_and_survives_restart(self):
        self.entries([(x, "2026-10-01T09:00:00+08:00") for x in ("c", "a", "b", "d", "e")])
        first = self.page()["data"]
        self.assertEqual([x["transaction_id"] for x in first["transactions"]], ["a", "b"])
        self.entries([("aa_new", "2026-10-01T09:00:00+08:00"), ("z_new", "2026-10-01T09:00:00+08:00")])
        self.gateway = BankGateway(Store(self.store.path))
        second = self.page(cursor=first["next_cursor"])["data"]
        self.assertEqual([x["transaction_id"] for x in second["transactions"]], ["c", "d"])
        third = self.page(cursor=second["next_cursor"])["data"]
        self.assertEqual([x["transaction_id"] for x in third["transactions"]], ["e"])
        self.assertIsNone(third["next_cursor"])
        self.assertEqual(len(self.page(limit=100)["data"]["transactions"]), 7)
        self.assertEqual(self.page(cursor=first["next_cursor"])["data"]["transactions"], second["transactions"])

    def test_cursor_rejects_changed_filters_account_owner_and_unknown_token(self):
        self.entries([(str(i), "2026-10-01T09:00:00Z") for i in range(3)])
        cursor = self.page()["data"]["next_cursor"]
        for change in ({"start_date":"2026-10-01"}, {"end_date":"2026-10-15"}, {"cursor":"page_missing"}):
            result = self.page(**({"cursor":cursor} | change))
            self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
        with self.store.connection(write=True) as c:
            c.execute("INSERT INTO accounts VALUES ('demo_account_second','demo_user_a','第二账户','CNY',0,0,0,0)")
        self.assertEqual(self.page(cursor=cursor, account_id="demo_account_second")["error"]["code"], "INVALID_REQUEST")
        params = dict(account_id="demo_account_li", start_date="2026-09-01", end_date="2026-10-31", cursor=cursor)
        self.assertEqual(self.query("transaction.list", params, principal="demo_user_li")["error"]["code"], "INVALID_REQUEST")

    def test_shanghai_date_boundary_preserves_microseconds_and_supported_date_extremes(self):
        self.entries([("sep_last", "2026-09-30T15:59:59.999999Z"),
                      ("oct_first", "2026-09-30T16:00:00Z"), ("oct_last", "2026-10-31T23:59:59.999999+08:00"),
                      ("nov_first", "2026-11-01T00:00:00+08:00")])
        rows = self.page(start_date="2026-10-01", end_date="2026-10-31", limit=100)["data"]["transactions"]
        self.assertEqual([x["transaction_id"] for x in rows], ["oct_first", "oct_last"])
        rows = self.page(start_date="0001-01-01", end_date="9999-12-31", limit=100)["data"]["transactions"]
        self.assertEqual(len(rows), 4)

    def test_default_page_limit_is_100_and_remaining_rows_are_retrievable(self):
        self.entries([(f"entry_{i:03d}", "2026-10-01T00:00:00Z") for i in range(105)])
        params = dict(account_id="demo_account_a", start_date="2026-10-01", end_date="2026-10-01")
        first = self.query("transaction.list", params)["data"]
        self.assertEqual(len(first["transactions"]), 100)
        second = self.query("transaction.list", params | {"cursor":first["next_cursor"]})["data"]
        self.assertEqual(len(second["transactions"]), 5)
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(len({x["transaction_id"] for x in first["transactions"] + second["transactions"]}), 105)

    def test_invalid_requests_raise_safe_errors_and_pending_does_not_debit(self):
        with self.assertRaises(ServiceError) as caught:
            self.gateway.execute({"schema_version":"1.0"}, principal_id="demo_user_a")
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")
        request = self.request("transfer.create", {"account_id":"demo_account_a","payee_id":"demo_payee_li","amount_minor":20000}, key="no_write")
        result = self.gateway.execute(request, principal_id="demo_user_a")
        self.assertEqual(result["status"], "awaiting_confirmation")
        with self.assertRaises(ServiceError):
            self.gateway.authorize(dict(schema_version="1.1", challenge_id="missing",
                operation_digest="0"*64, decision="approve"), principal_id="demo_user_a")
        self.assertEqual(self.balance()["data"]["balance_minor"], 200000)
        with self.store.connection() as c:
            for table in ("transfers","ledger_entries","fund_postings"):
                self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM challenges').fetchone()[0], 1)

    def test_schema_rejects_bad_balances_foreign_keys_and_duplicate_operation_keys(self):
        for sql in ("UPDATE accounts SET balance_minor=-1", "UPDATE accounts SET available_minor=0 WHERE account_id='demo_account_a'",
                    "UPDATE accounts SET principal_id='missing'"):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.connection(write=True) as c:
                    c.execute(sql)
        self.operation()
        with self.assertRaises(sqlite3.IntegrityError):
            self.operation()
        self.assertEqual(self.balance()["data"]["balance_minor"], 200000)

    def test_foreign_or_newer_database_is_not_overwritten(self):
        other = self.root / "other.sqlite3"
        with closing(sqlite3.connect(other)) as c:
            c.execute("CREATE TABLE private_data(value TEXT)")
            c.execute("INSERT INTO private_data VALUES ('preserve')")
            c.commit()
        with self.assertRaises(ServiceError):
            initialize(Store(other))
        with closing(sqlite3.connect(other)) as c:
            self.assertEqual(c.execute("SELECT value FROM private_data").fetchone()[0], "preserve")
        with self.store.connection(write=True) as c:
            c.execute("UPDATE metadata SET value='99' WHERE key='seed_version'")
        with self.assertRaises(ServiceError):
            initialize(self.store)
        with self.store.connection(write=True) as c:
            c.execute("PRAGMA user_version=99")
        with self.assertRaises(ServiceError):
            initialize(self.store)

    def test_reset_requires_confirmation_and_only_targets_fixed_demo_database(self):
        self.entries([("preserve_other_database", "2026-10-01T00:00:00Z")])
        with patch("bank.seed.PROJECT_ROOT", self.root):
            target = Store(demo_path())
            initialize(target)
            with target.connection(write=True) as c:
                c.execute("UPDATE accounts SET balance_minor=12300,available_minor=12300 WHERE account_id='demo_account_a'")
                c.execute("INSERT INTO page_cursors VALUES ('old','demo_user_a','demo_account_a','2026-01-01','2026-12-31',0,'','')")
            with self.assertRaises(ServiceError):
                reset_demo()
            reset_demo(confirm=True)
            with target.connection() as c:
                self.assertEqual(c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0], 200000)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM page_cursors").fetchone()[0], 0)
            self.assertEqual(self.balance()["data"]["balance_minor"], 200100)

    def test_reset_failure_rolls_back_and_leaves_old_data(self):
        with patch("bank.seed.PROJECT_ROOT", self.root):
            target = Store(demo_path())
            initialize(target)
            with target.connection(write=True) as c:
                c.execute("UPDATE accounts SET balance_minor=12300,available_minor=12300 WHERE account_id='demo_account_a'")
            with patch("bank.seed._seed", side_effect=RuntimeError("injected")):
                with self.assertRaises(RuntimeError):
                    reset_demo(confirm=True)
            with target.connection() as c:
                self.assertEqual(c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0], 12300)

    def test_linked_reset_path_is_rejected_without_touching_target(self):
        import os
        (self.root / "runtime").mkdir()
        os.link(self.store.path, self.root / "runtime/sandbox.sqlite3")
        with patch("bank.seed.PROJECT_ROOT", self.root):
            with self.assertRaises(ServiceError):
                reset_demo(confirm=True)
        self.assertEqual(self.balance()["data"]["balance_minor"], 200000)


if __name__ == "__main__":
    unittest.main()
