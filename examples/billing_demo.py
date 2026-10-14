"""B2 独立临时库演示；四条虚构已记账历史，不导入日常运行库。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from bank.gateway import BankGateway
from bank.store import Store, utc_timestamp
from shared import adapt_request, validate_result


def main():
    with TemporaryDirectory() as directory:
        store = Store(Path(directory) / 'billing.sqlite3')
        store.initialize()
        with store.connection(write=True) as c:
            c.execute("INSERT INTO principals VALUES ('billing_user','虚构账单用户')")
            # 期初 1000 - 消费100 - 订阅20 - 转出200 + 退款30 = 710 元。
            c.execute("INSERT INTO accounts VALUES ('billing_account','billing_user','虚构账户','CNY',100000,71000,71000,0)")
            c.execute("INSERT INTO principals VALUES ('recipient','虚构收款用户')")
            c.execute("INSERT INTO accounts VALUES ('recipient_account','recipient','虚构收款账户','CNY',0,20000,20000,0)")
            rows = [('expense', 'billing_account', 10000, 'debit', 'consumption', '餐饮'),
                    ('subscription', 'billing_account', 2000, 'debit', 'subscription', '影音会员'),
                    ('transfer_out', 'billing_account', 20000, 'debit', 'transfer', '转账'),
                    ('refund', 'billing_account', 3000, 'credit', 'refund', '退款'),
                    ('transfer_in', 'recipient_account', 20000, 'credit', 'transfer', '转账')]
            for key, account, amount, direction, kind, category in rows:
                c.execute('''INSERT INTO ledger_entries (transaction_id,account_id,reference_id,posting_key,
                    amount_minor,direction,kind,category,occurred_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                    (key, account, 'paired_transfer' if kind == 'transfer' else key, key,
                     amount, direction, kind, category, utc_timestamp('2026-08-01T12:00:00+08:00')))
        gateway = BankGateway(store)
        request = adapt_request('bill.analyze', dict(account_id='billing_account', start_date='2026-08-01',
            end_date='2026-08-31'), request_id='billing_demo', trace_id='billing_demo')
        before = store.path.read_bytes()
        result = gateway.execute(request, principal_id='billing_user')
        validate_result(request, result)
        assert result['status'] == 'succeeded' and result['data']['total_expense_minor'] == 12000
        assert result['data']['categories'] == [dict(category='影音会员', amount_minor=2000),
                                               dict(category='餐饮', amount_minor=10000)]
        assert '退款入账 30.00 元' in result['data']['report']
        denied = gateway.execute(request, principal_id='recipient')
        validate_result(request, denied)
        assert denied['status'] == 'failed' and denied['error']['code'] == 'FORBIDDEN'
        assert before == store.path.read_bytes()
        print('隔离虚构历史；毛支出 120.00 元，退款另列 30.00 元；越权拒绝、数据库未变。')
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
