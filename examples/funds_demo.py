"""P2 工程验收：只在临时库模拟可信网关调用；不是用户授权或正式扣款入口。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from bank.seed import initialize
from bank.store import Store
from bank.services import SandboxServices
from bank.transfer import handle
from shared import adapt_request, adapt_result


def main():
    with TemporaryDirectory(prefix="fintech-p2-") as directory:
        store = Store(Path(directory) / "sandbox.sqlite3")
        initialize(store)
        services = SandboxServices(store)
        request = adapt_request("transfer.create",
            dict(account_id="demo_account_a", payee_id="demo_payee_li", amount_minor=20000),
            request_id="demo_p2_request", trace_id="demo_p2_trace", idempotency_key="demo_p2_operation")
        results = []
        for request_id in ("demo_p2_request", "demo_p2_retry"):
            attempt = request | {"request_id": request_id}
            with store.connection(write=True) as connection:
                with services.transfer_context(connection, attempt, principal_id="demo_user_a") as context:
                    raw = handle(attempt["params"], context=context)
            results.append(adapt_result(attempt, raw, source="sandbox"))
        with store.connection() as connection:
            balances = {row[0]: row[1] for row in connection.execute("SELECT account_id,balance_minor FROM accounts")}
            counts = {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                      for name in ("transfers", "ledger_entries", "fund_postings", "operations", "audit_events")}
        assert balances["demo_account_a"] == 180000 and balances["demo_account_li"] == 20000
        assert sum(balances.values()) == 200000
        assert counts == dict(transfers=1, ledger_entries=2, fund_postings=2, operations=1, audit_events=1)
        assert results[0]["data"] == results[1]["data"]
        print(json.dumps(dict(description="仅临时库内部验收；尚未接用户授权，不修改日常演示库",
            amount_minor=20000, balances_minor=balances, persisted_counts=counts,
            retry_same_transaction=True), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
