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
STORAGE_VERSION = 13  # 数据库版本，与公开 JSON schema_version 分开。

SUBSCRIPTION_CHARGE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS subscription_charges (
        charge_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
        subscription_id TEXT NOT NULL REFERENCES subscriptions, charge_date TEXT NOT NULL,
        amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
        entry_id TEXT NOT NULL REFERENCES ledger_entries(transaction_id),
        operation_key TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(principal_id, operation_key), UNIQUE(subscription_id, charge_date))""",
)

WEALTH_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS risk_profiles (
        profile_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
        questionnaire_version TEXT NOT NULL, answers_json TEXT NOT NULL,
        total_score INTEGER NOT NULL CHECK(total_score BETWEEN 0 AND 100),
        risk_level TEXT NOT NULL CHECK(risk_level IN ('R1','R2','R3','R4','R5')),
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS holdings (
        holding_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
        account_id TEXT NOT NULL REFERENCES accounts, product_id TEXT NOT NULL,
        units TEXT NOT NULL, confirmed_on TEXT NOT NULL,
        lock_days INTEGER NOT NULL CHECK(lock_days >= 0),
        state TEXT NOT NULL CHECK(state IN ('open','closed')))""",
    """CREATE INDEX IF NOT EXISTS holdings_account ON holdings(account_id, state)""",
    """CREATE TABLE IF NOT EXISTS wealth_orders (
        order_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
        account_id TEXT NOT NULL REFERENCES accounts, product_id TEXT NOT NULL,
        order_type TEXT NOT NULL CHECK(order_type IN ('purchase','redeem')),
        amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
        fee_minor INTEGER NOT NULL CHECK(fee_minor >= 0),
        units TEXT NOT NULL, holding_id TEXT, status TEXT NOT NULL,
        operation_key TEXT NOT NULL, occurred_at TEXT NOT NULL,
        UNIQUE(principal_id, order_type, operation_key))""",
)

WORKFLOWS_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS merchant_intents (
        principal_id TEXT NOT NULL REFERENCES principals, operation_key TEXT NOT NULL,
        reservation_id TEXT NOT NULL REFERENCES reservations, amount_minor INTEGER NOT NULL CHECK(amount_minor>0),
        PRIMARY KEY(principal_id,operation_key))""",
    """CREATE TABLE IF NOT EXISTS merchant_orders (
        order_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
        operation_key TEXT NOT NULL, reservation_id TEXT NOT NULL REFERENCES reservations,
        account_id TEXT NOT NULL REFERENCES accounts, card_id TEXT NOT NULL REFERENCES cards,
        item_id TEXT NOT NULL, amount_minor INTEGER NOT NULL CHECK(amount_minor>0),
        deliver_on TEXT NOT NULL, delivery_address_id TEXT NOT NULL, entry_id TEXT NOT NULL REFERENCES ledger_entries(transaction_id),
        UNIQUE(principal_id,operation_key))""",
    """CREATE TABLE IF NOT EXISTS card_applications (
        application_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts,
        principal_id TEXT NOT NULL REFERENCES principals, operation_key TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','rejected','cancelled')), UNIQUE(principal_id,operation_key))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS one_pending_application ON card_applications(account_id) WHERE state='pending'""",
    """CREATE TABLE IF NOT EXISTS subscription_reminders (
        reminder_id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL REFERENCES subscriptions,
        principal_id TEXT NOT NULL REFERENCES principals, charge_date TEXT NOT NULL,
        remind_at TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('scheduled','sent','cancelled')),
        UNIQUE(subscription_id,charge_date))""",
)

RESERVATIONS_SCHEMA = f"""CREATE TABLE IF NOT EXISTS reservations (
    reservation_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
    account_id TEXT NOT NULL REFERENCES accounts, reserve_key TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor BETWEEN 1 AND {MAX_SAFE_INTEGER}),
    remaining_reserved_minor INTEGER NOT NULL CHECK(typeof(remaining_reserved_minor)='integer'
        AND remaining_reserved_minor BETWEEN 0 AND amount_minor),
    purpose TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('active','partially_consumed','frozen','released','closed')),
    UNIQUE(principal_id,reserve_key),
    CHECK(state NOT IN ('released','closed') OR remaining_reserved_minor=0))"""

TASKS_SCHEMA = """CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
    task_key TEXT NOT NULL, spec_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('scheduled','running','dispatched','failed','unknown','cancelled')),
    payee_account_id TEXT NOT NULL REFERENCES accounts, failure_reason TEXT,
    UNIQUE(principal_id,task_key))"""

SUBSCRIPTIONS_SCHEMA = f"""CREATE TABLE IF NOT EXISTS subscriptions (
    subscription_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts,
    merchant_name TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor BETWEEN 1 AND {MAX_SAFE_INTEGER}),
    interval TEXT NOT NULL CHECK(interval IN ('weekly','monthly','yearly')), next_charge_date TEXT NOT NULL,
    subscription_state TEXT NOT NULL CHECK(subscription_state IN ('candidate','active','dismissed','cancelled')),
    evidence TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0))"""

COLLECTIONS_SCHEMA = f"""CREATE TABLE IF NOT EXISTS collections (
    collection_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
    account_id TEXT NOT NULL REFERENCES accounts, idempotency_key TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor BETWEEN 1 AND {MAX_SAFE_INTEGER}),
    shares_json TEXT NOT NULL, occurred_at TEXT NOT NULL,
    UNIQUE(principal_id,idempotency_key))"""

CARDS_SCHEMA = f"""CREATE TABLE IF NOT EXISTS cards (
    card_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts,
    card_state TEXT NOT NULL CHECK(card_state IN ('active','frozen','lost')),
    limit_minor INTEGER NOT NULL CHECK(typeof(limit_minor)='integer' AND limit_minor BETWEEN 0 AND {MAX_SAFE_INTEGER}),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0))"""

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
        payee_account_id TEXT, resource_binding_json TEXT,
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
    CARDS_SCHEMA,
    COLLECTIONS_SCHEMA,
    SUBSCRIPTIONS_SCHEMA,
    TASKS_SCHEMA,
    RESERVATIONS_SCHEMA,
    *WORKFLOWS_SCHEMA,
    *WEALTH_SCHEMA,
    *SUBSCRIPTION_CHARGE_SCHEMA,
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
                version = 3
            if app_id == APPLICATION_ID and version == 3:
                columns = {row['name'] for row in connection.execute('PRAGMA table_info(challenges)')}
                if 'resource_binding_json' not in columns:
                    connection.execute('ALTER TABLE challenges ADD COLUMN resource_binding_json TEXT')
                version = 4
            if app_id == APPLICATION_ID and version == 4:
                connection.execute(CARDS_SCHEMA)
                version = 5
            if app_id == APPLICATION_ID and version == 5:
                connection.execute(COLLECTIONS_SCHEMA)
                version = 6
            if app_id == APPLICATION_ID and version == 6:
                connection.execute(SUBSCRIPTIONS_SCHEMA)
                version = 7
            if app_id == APPLICATION_ID and version == 7:
                connection.execute(TASKS_SCHEMA)
                version = 8
            if app_id == APPLICATION_ID and version == 8:
                connection.execute(RESERVATIONS_SCHEMA)
                version = 9
            if app_id == APPLICATION_ID and version == 9:
                for statement in WORKFLOWS_SCHEMA:
                    connection.execute(statement)
                version = 10
            if app_id == APPLICATION_ID and version == 10:
                for statement in WEALTH_SCHEMA:
                    connection.execute(statement)
                version = 11
            if app_id == APPLICATION_ID and version == 11:
                for statement in SUBSCRIPTION_CHARGE_SCHEMA:
                    connection.execute(statement)
                version = 12
            if app_id == APPLICATION_ID and version == 12:
                # 原子复制旧订单，修正与网关(principal, action, key)不一致的唯一范围。
                connection.execute(WEALTH_SCHEMA[-1].replace('wealth_orders (','wealth_orders_v13 (',1))
                connection.execute('INSERT INTO wealth_orders_v13 SELECT * FROM wealth_orders')
                connection.execute('DROP TABLE wealth_orders')
                connection.execute('ALTER TABLE wealth_orders_v13 RENAME TO wealth_orders')
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
