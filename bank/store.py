"""SQLite 存储与事务。路径只由可信服务端配置，不能来自模型参数。"""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

from shared import ServiceError
from shared.contracts import MAX_SAFE_INTEGER, check_value

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "runtime" / "sandbox.sqlite3"
APPLICATION_ID = 0x46494E31
STORAGE_VERSION = 3  # 数据库版本，与公开 JSON schema_version 分开。

FUNDS_SCHEMA = """CREATE TABLE fund_postings (
    posting_key TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
    entry_id TEXT NOT NULL UNIQUE REFERENCES ledger_entries(transaction_id),
    command_json TEXT NOT NULL, receipt_json TEXT NOT NULL)"""

SCHEMA = (
    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE principals (principal_id TEXT PRIMARY KEY, display_name TEXT NOT NULL)",
    f"""CREATE TABLE accounts (
        account_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
        display_name TEXT NOT NULL, currency TEXT NOT NULL CHECK(currency='CNY'),
        opening_balance_minor INTEGER NOT NULL CHECK(typeof(opening_balance_minor)='integer'
            AND opening_balance_minor BETWEEN 0 AND {MAX_SAFE_INTEGER}),
        balance_minor INTEGER NOT NULL CHECK(typeof(balance_minor)='integer'
            AND balance_minor BETWEEN 0 AND {MAX_SAFE_INTEGER}),
        available_minor INTEGER NOT NULL CHECK(typeof(available_minor)='integer'
            AND available_minor BETWEEN 0 AND {MAX_SAFE_INTEGER}),
        reserved_minor INTEGER NOT NULL CHECK(typeof(reserved_minor)='integer'
            AND reserved_minor BETWEEN 0 AND {MAX_SAFE_INTEGER}),
        CHECK(balance_minor=available_minor+reserved_minor))""",
    """CREATE TABLE payees (
        principal_id TEXT NOT NULL REFERENCES principals, payee_id TEXT NOT NULL,
        account_id TEXT NOT NULL REFERENCES accounts, display_name TEXT NOT NULL,
        masked_account TEXT NOT NULL, bank_name TEXT, phone TEXT,
        PRIMARY KEY(principal_id, payee_id))""",
    """CREATE TABLE operations (
        principal_id TEXT NOT NULL REFERENCES principals, action TEXT NOT NULL,
        idempotency_key TEXT NOT NULL, digest TEXT NOT NULL,
        request_json TEXT NOT NULL, result_json TEXT NOT NULL,
        PRIMARY KEY(principal_id, action, idempotency_key))""",
    f"""CREATE TABLE transfers (
        transaction_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
        account_id TEXT NOT NULL REFERENCES accounts, payee_id TEXT NOT NULL,
        amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer'
            AND amount_minor BETWEEN 1 AND {MAX_SAFE_INTEGER}),
        occurred_at TEXT NOT NULL,
        FOREIGN KEY(principal_id, payee_id) REFERENCES payees(principal_id, payee_id))""",
    f"""CREATE TABLE ledger_entries (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id TEXT NOT NULL UNIQUE,
        account_id TEXT NOT NULL REFERENCES accounts, reference_id TEXT NOT NULL,
        posting_key TEXT NOT NULL UNIQUE,
        amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer'
            AND amount_minor BETWEEN 1 AND {MAX_SAFE_INTEGER}),
        direction TEXT NOT NULL CHECK(direction IN ('credit','debit')),
        kind TEXT NOT NULL CHECK(kind IN ('income','consumption','transfer','refund','wealth','subscription')),
        category TEXT NOT NULL, occurred_at TEXT NOT NULL
            CHECK(length(occurred_at)=27 AND substr(occurred_at,27,1)='Z'),
        merchant_name TEXT)""",
    "CREATE INDEX ledger_account_time ON ledger_entries(account_id, occurred_at, transaction_id)",
    """CREATE TABLE challenges (
        challenge_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
        action TEXT NOT NULL, idempotency_key TEXT NOT NULL, digest TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('confirm','verify')), expires_at TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','used','expired','cancelled','blocked')),
        proof_digest TEXT, failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK(failed_attempts>=0),
        payee_account_id TEXT,
        FOREIGN KEY(principal_id,action,idempotency_key)
            REFERENCES operations(principal_id,action,idempotency_key))""",
    """CREATE TABLE audit_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, principal_id TEXT NOT NULL REFERENCES principals,
        trace_id TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL,
        occurred_at TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}')""",
    """CREATE TABLE page_cursors (
        token TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
        account_id TEXT NOT NULL REFERENCES accounts, start_date TEXT NOT NULL,
        end_date TEXT NOT NULL, upper_seq INTEGER NOT NULL,
        after_time TEXT NOT NULL, after_id TEXT NOT NULL)""",
    FUNDS_SCHEMA,
)


def utc_timestamp(value):
    """已提交流水统一保存 UTC 微秒字符串，保证同一时刻和排序口径一致。"""
    check_value(value, "datetime", "occurred_at")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class Store:
    def __init__(self, path=DEFAULT_DB_PATH):
        self.path = Path(path).absolute()
        self._write_connections = set()

    def _connect(self, *, create=False):
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # mode=rw 防止查询一个拼错的路径时悄悄新建空库。
        uri = self.path.as_uri() + ("?mode=rwc" if create else "?mode=rw")
        connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _check_identity(connection):
        if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != STORAGE_VERSION):
            raise ServiceError("INVALID_STATE", "数据库未初始化或存储版本不兼容。")

    def initialize(self):
        """只为全新空库创建结构，不修改不属于本项目或未知版本的库。"""
        connection = self._connect(create=True)
        try:
            connection.execute("BEGIN IMMEDIATE")
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            app_id = connection.execute("PRAGMA application_id").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if app_id == APPLICATION_ID and version == 1:
                required = {"metadata", "principals", "accounts", "payees", "operations", "transfers",
                            "ledger_entries", "challenges", "audit_events", "page_cursors"}
                if not required.issubset({row["name"] for row in tables}):
                    raise ServiceError("INVALID_STATE", "P1 数据库结构不完整，拒绝自动升级。")
                # P1 → P2 只增加子记账回执表；与版本号在同一事务提交。
                connection.execute(FUNDS_SCHEMA)
                version = 2
            if app_id == APPLICATION_ID and version == 2:
                columns = {row['name'] for row in connection.execute('PRAGMA table_info(challenges)')}
                if 'payee_account_id' not in columns:
                    connection.execute('ALTER TABLE challenges ADD COLUMN payee_account_id TEXT')
                connection.execute(f"PRAGMA user_version={STORAGE_VERSION}")
            elif tables or app_id or version:
                self._check_identity(connection)
            else:
                for statement in SCHEMA:
                    connection.execute(statement)
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={STORAGE_VERSION}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def connection(self, *, write=False):
        """内部事务入口；业务/模型请求不能携带或制造此连接。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._check_identity(connection)
            if write:
                self._write_connections.add(connection)
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            self._write_connections.discard(connection)
            connection.close()

    def require_write_transaction(self, connection):
        if not isinstance(connection, sqlite3.Connection) or connection not in self._write_connections:
            raise ServiceError("FORBIDDEN", "需要本存储创建的有效写事务。")
        try:
            active = connection.in_transaction
        except sqlite3.Error:
            active = False
        if not active:
            raise ServiceError("FORBIDDEN", "写事务已经结束。")

    @contextmanager
    def savepoint(self, connection):
        """局部错误即使被调用者捕获也不遗留半次写入；不提交外层事务。"""
        self.require_write_transaction(connection)
        name = "sp_" + uuid4().hex
        connection.execute(f"SAVEPOINT {name}")
        try:
            yield
            connection.execute(f"RELEASE {name}")
        except BaseException:
            connection.execute(f"ROLLBACK TO {name}")
            connection.execute(f"RELEASE {name}")
            raise
