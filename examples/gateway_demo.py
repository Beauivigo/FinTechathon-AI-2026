"""P3 公共网关演示。仅临时库、模拟身份/验证，不改日常演示库。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import adapt_request, validate_result


def main():
    with TemporaryDirectory(prefix='fintech-p3-') as directory:
        store = Store(Path(directory) / 'bank.sqlite3')
        initialize(store)
        gateway = BankGateway(store)
        results = []
        for index, amount in enumerate((20000, 90000), 1):
            request = adapt_request('transfer.create', dict(account_id='demo_account_a',
                payee_id='demo_payee_li', amount_minor=amount), request_id=f'req_{index}',
                trace_id=f'trace_{index}', idempotency_key=f'demo_p3_{index}')
            pending = gateway.execute(request, principal_id='demo_user_a')
            n = pending['next_action']
            payload = dict(schema_version='1.1', challenge_id=n['challenge_id'],
                operation_digest=n['operation_digest'], decision='approve')
            if n['kind'] == 'verify':
                payload['proof_token'] = gateway.issue_demo_proof(n['challenge_id'], principal_id='demo_user_a')
            done = gateway.authorize(payload, principal_id='demo_user_a')
            validate_result(request, done)
            assert done['status'] == 'succeeded'
            assert gateway.authorize(payload, principal_id='demo_user_a') == done
            results.append(dict(amount_minor=amount, before=pending['status'], after=done['status'], replay_same=True))
        with store.connection() as c:
            balances = {r[0]: r[1] for r in c.execute('SELECT account_id,balance_minor FROM accounts')}
            counts = {name: c.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                      for name in ('transfers', 'ledger_entries', 'fund_postings')}
        assert balances['demo_account_a'] == 90000 and balances['demo_account_li'] == 110000
        assert sum(balances.values()) == 200000
        assert counts == dict(transfers=2, ledger_entries=4, fund_postings=4)
        print(json.dumps(dict(description='临时库：200 元确认 + 900 元模拟强验证；不输出凭证明文',
            operations=results, balances_minor=balances, counts=counts), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
