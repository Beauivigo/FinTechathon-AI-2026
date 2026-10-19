"""固定虚构数据与管理员命令；不会由页面刷新自动重置。"""

import argparse
import json
import sqlite3

from shared import ServiceError
from .store import PROJECT_ROOT, Store

PRINCIPALS = (
    ("demo_user_a", "模拟用户 A"), ("demo_user_li", "李明"),
    ("demo_user_wang_1", "小王一"), ("demo_user_wang_2", "小王二"),
)
ACCOUNTS = (
    ("demo_account_a", "demo_user_a", "模拟日常账户", "CNY", 200000, 200000, 200000, 0),
    ("demo_account_li", "demo_user_li", "李明模拟账户", "CNY", 0, 0, 0, 0),
    ("demo_account_wang_1", "demo_user_wang_1", "小王一模拟账户", "CNY", 0, 0, 0, 0),
    ("demo_account_wang_2", "demo_user_wang_2", "小王二模拟账户", "CNY", 0, 0, 0, 0),
)
PAYEES = (
    ("demo_user_a", "demo_payee_li", "demo_account_li", "李明", "****9012", "模拟银行甲", "00000000001"),
    ("demo_user_a", "demo_payee_wang_1", "demo_account_wang_1", "小王", "****1234", "模拟银行甲", "00000000002"),
    ("demo_user_a", "demo_payee_wang_2", "demo_account_wang_2", "小王", "****5678", "模拟银行乙", "00000000003"),
)


def demo_path():
    """固定目标，拒绝 runtime/数据库被符号链接或目录联接引向其他位置。"""
    root = PROJECT_ROOT.resolve()
    path = root / "runtime" / "sandbox.sqlite3"
    if path.resolve() != path or path.is_symlink() or path.parent.is_symlink():
        raise ServiceError("FORBIDDEN", "演示数据库路径不得指向项目外或链接位置。")
    if path.exists() and path.stat().st_nlink > 1:
        raise ServiceError("FORBIDDEN", "演示数据库不能使用硬链接。")
    return path


def _seed(connection):
    existing = connection.execute("SELECT value FROM metadata WHERE key='seed_version'").fetchone()
    if existing:
        if existing["value"] != "1":
            raise ServiceError("INVALID_STATE", "种子版本不兼容，拒绝自动覆盖。")
        return False
    if connection.execute("SELECT 1 FROM principals LIMIT 1").fetchone():
        raise ServiceError("INVALID_STATE", "已有数据缺少种子版本，拒绝自动覆盖。")
    connection.executemany("INSERT INTO principals VALUES (?,?)", PRINCIPALS)
    connection.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)", ACCOUNTS)
    connection.executemany("INSERT INTO payees VALUES (?,?,?,?,?,?,?)", PAYEES)
    connection.execute("INSERT INTO metadata VALUES ('seed_version','1')")
    return True


def initialize(store):
    """可信服务端可传独立测试库；首次导入，后续保留全部运行状态。"""
    store.initialize()
    with store.connection(write=True) as connection:
        created = _seed(connection)
        _seed_cards(connection)
        _seed_subscriptions(connection)
        return created


def _seed_cards(connection):
    existing = connection.execute("SELECT value FROM metadata WHERE key='cards_seed_version'").fetchone()
    if existing:
        if existing[0] not in ('1', '2'):
            raise ServiceError('INVALID_STATE', '模拟卡种子版本不兼容。')
        return
    connection.executemany('INSERT INTO cards(card_id,account_id,card_state,limit_minor) VALUES (?,?,?,?)',
        [('CARD-E001', 'demo_account_a', 'active', 50000),
         ('CARD-E002', 'demo_account_a', 'frozen', 20000),
         ('CARD-E003', 'demo_account_a', 'lost', 30000)])
    connection.execute("INSERT INTO metadata VALUES ('cards_seed_version','2')")


def _seed_subscriptions(connection):
    existing = connection.execute("SELECT value FROM metadata WHERE key='subscriptions_seed_version'").fetchone()
    if existing:
        if existing[0] != '1':
            raise ServiceError('INVALID_STATE','订阅种子版本不兼容。')
        return
    connection.executemany('''INSERT INTO subscriptions
        (subscription_id,account_id,merchant_name,amount_minor,interval,next_charge_date,subscription_state,evidence)
        VALUES (?,?,?,?,?,?,?,?)''',[
        ('SUB-E001','demo_account_a','模拟影音会员',2500,'monthly','2026-11-05','candidate',
         'E V1.0 固定模拟样例：P-1/P-2/P-3，2026-08-05、09-05、10-05各2500分；非自动识别。'),
        ('SUB-E003','demo_account_a','模拟健身年卡',120000,'yearly','2027-01-10','active',
         'E V1.0 已知订阅（已核实），固定模拟样例。')])
    connection.execute("INSERT INTO metadata VALUES ('subscriptions_seed_version','1')")


def reset_demo(*, confirm=False):
    """仅清空固定演示库的业务表并重建种子；不接受任意路径、不删除文件。"""
    if confirm is not True:
        raise ServiceError("INVALID_REQUEST", "重置会清除演示业务，请显式确认。")
    path = demo_path()
    if not path.exists():
        raise ServiceError("INVALID_STATE", "请先初始化演示数据库。")
    store = Store(path)
    with store.connection(write=True) as connection:
        for table in ("wealth_orders", "holdings", "risk_profiles", "merchant_orders", "merchant_intents", "card_applications", "subscription_reminders", "page_cursors", "challenges", "audit_events", "fund_postings", "ledger_entries",
                      "transfers", "collections", "tasks", "reservations", "operations", "cards", "subscriptions", "payees", "accounts", "principals", "metadata"):
            connection.execute(f"DELETE FROM {table}")  # 固定内部表名，非用户输入。
        _seed(connection)
        _seed_cards(connection)
        _seed_subscriptions(connection)


def main():
    parser = argparse.ArgumentParser(description="P1 本地模拟银行；固定使用项目 runtime/sandbox.sqlite3")
    parser.add_argument("command", choices=("init", "show", "reset"))
    parser.add_argument("--confirm-reset", action="store_true", help="确认清空并重置固定演示库")
    args = parser.parse_args()
    try:
        store = Store(demo_path())
        if args.command == "init":
            created = initialize(store)
            print("已初始化虚构账户；未发生转账。" if created else "数据库已初始化，原数据保持不变。")
        elif args.command == "reset":
            reset_demo(confirm=args.confirm_reset)
            print("仅本项目演示库已重置：A 2000 元、收款账户 0 元；旧演示记录已清除。")
        else:
            from .gateway import BankGateway
            from shared import adapt_request
            gateway = BankGateway(store)
            request = adapt_request("account.balance", {"account_id": "demo_account_a"},
                                    request_id="cli_balance", trace_id="cli_trace")
            result = gateway.execute(request, principal_id="demo_user_a")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "succeeded" else 1
    except (ServiceError, sqlite3.Error, OSError):
        print("操作未完成：请检查数据库已初始化、路径与版本正确；重置须加 --confirm-reset。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
