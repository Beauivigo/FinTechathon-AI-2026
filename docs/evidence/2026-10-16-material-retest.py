"""A 资料复测：临时库、真实网关/账单/页面；不改日常运行库或成员原件。

运行：.venv/Scripts/python.exe -B -X utf8 docs/evidence/2026-10-16-material-retest.py
依赖仓库相邻的 群组资料/B、C、E 本轮原稿；结果保存到本文件同目录。
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from copy import deepcopy
from hashlib import sha256
import json, sys, subprocess, unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from billing import analyze
from material_check import approved
from material_check.reader import read_workbook
from shared import adapt_request, ServiceError

OUT = Path(__file__).parent
SRC = ROOT.parent / '群组资料'
C_SOURCE = SRC / 'C/账单数据与手算（C项目）V1.3.xlsx'
B_SOURCE = SRC / 'B/资金规则与功能复核（B项目）V1.2.xlsx'
E_SOURCE = SRC / 'E/E部分交付材料_卡片订阅规则与交互文案(1).docx'
runtime = ROOT / 'runtime/sandbox.sqlite3'
runtime_before = sha256(runtime.read_bytes()).hexdigest() if runtime.exists() else None
source_hashes={str(p):sha256(p.read_bytes()).hexdigest() for p in (B_SOURCE,C_SOURCE,E_SOURCE)}
result=dict(date='2026-10-16',executor='A（本次代测，非B/C/E历史原始记录）',
            commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            python=sys.version,source_sha256=source_hashes,cases=[],limits=[
                'C V1.3仅在测试进程临时绑定；日常页面仍固定B V1.1/C V1.2。',
                'E CARD-E数据仅写临时库，未替换日常演示种子。',
                '未实现的预算/定时/商户/订阅及自动异常没有业务通过结论。',
                '既有测试复跑覆盖其断言，不复原缺失的B/C历史脚本或截图。'])
def record(case, actual): result['cases'].append(dict(case=case,status='passed',actual=actual))
parsed=read_workbook(C_SOURCE)
transactions, provenance=approved._convert(parsed)
assert len(transactions)==15
expected={('ACCT-C001','08'):(46490,0,{'交通':5000,'日用百货':32850,'餐饮':8640}),
          ('ACCT-C001','09'):(0,4500,{}),
          ('ACCT-C002','08'):(2500,29900,{'影音会员':2500}),
          ('ACCT-C002','09'):(256800,0,{'住房':250000,'云服务':6800}),
          ('ACCT-C003','08'):(0,0,{}),
          ('ACCT-C003','09'):(41260,0,{'日用百货':41260})}
with patch.multiple(approved,SOURCE=C_SOURCE,DECISION=B_SOURCE,
                    SOURCE_SHA=source_hashes[str(C_SOURCE)],DECISION_SHA=source_hashes[str(B_SOURCE)]):
    for (account,month),(expense,refund,categories) in expected.items():
        params=dict(account_id=account,start_date=f'2026-{month}-01',end_date=f'2026-{month}-'+('31' if month=='08' else '30'))
        actual=approved.query(params,principal_id='PRIN-C01')
        assert actual['bill']['total_expense_minor']==expense
        assert actual['refund_minor']==refund
        assert {c['category']:c['amount_minor'] for c in actual['bill']['categories']}==categories
        assert actual['bill']['anomalies']==[]
        record('C-'+account+'-'+month,actual)
    from streamlit.testing.v1 import AppTest
    with patch('bank.store.Store.initialize',side_effect=AssertionError('资料页面不可打开银行库')):
        page=AppTest.from_file(str(ROOT/'app/pages/1_资料账单.py')).run()
        page.button(key='material_query').click().run()
        assert not page.exception
        assert page.metric[0].value=='464.90'
        record('C-V1.3-真实资料页面',dict(metric=page.metric[0].value,exceptions=len(page.exception)))
for rid,stamp,month,expected_total,category in [
    ('SIM-B0831','2026-08-31T15:59:00Z','08',46491,'交通'),
    ('SIM-B0901','2026-08-31T16:00:00Z','09',1,'餐饮')]:
    extra=dict(transaction_id=rid,account_id='ACCT-C001',amount_minor=1,direction='debit',
               kind='consumption',category=category,occurred_at=stamp)
    actual=analyze(dict(account_id='ACCT-C001',start_date=f'2026-{month}-01',end_date=f'2026-{month}-'+('31' if month=='08' else '30')),transactions+[extra])
    assert actual['total_expense_minor']==expected_total
    record('C-'+rid,actual)
for field in ('金额（元）','日期'):
    bad=deepcopy(parsed);bad['ledger_rows'][0]['fields'][field]['value']=None
    try: approved._convert(bad)
    except ServiceError as exc:
        assert exc.code=='UPSTREAM_ERROR';record('C-缺失'+field,dict(error=exc.code))
    else: raise AssertionError('缺必填字段未拒绝')

# E提供的三张模拟卡，四个原始用例加挂失两种合法起点。
for case,action,card,expected_state,expected_status in [
    ('E-C1','card.freeze','CARD-E001','frozen','succeeded'),
    ('E-C2','card.unfreeze','CARD-E002','active','succeeded'),
    ('E-C3','card.unfreeze','CARD-E003','lost','failed'),
    ('E-C4','card.release_loss','CARD-E003','frozen','succeeded'),
    ('E-C5','card.report_loss','CARD-E001','lost','succeeded'),
    ('E-C6','card.report_loss','CARD-E002','lost','succeeded')]:
    with TemporaryDirectory() as td:
        store=Store(Path(td)/'bank.sqlite3');initialize(store)
        with store.connection(write=True) as c:
            c.execute('DELETE FROM cards')
            c.executemany('INSERT INTO cards(card_id,account_id,card_state,limit_minor) VALUES (?,?,?,?)',[
                ('CARD-E001','demo_account_a','active',50000),('CARD-E002','demo_account_a','frozen',20000),
                ('CARD-E003','demo_account_a','lost',30000)])
        gateway=BankGateway(store)
        request=adapt_request(action,dict(card_id=card),request_id=case,trace_id=case,idempotency_key=case)
        outcome=gateway.execute(request,principal_id='demo_user_a')
        if outcome['status']=='awaiting_verification':
            n=outcome['next_action']
            outcome=gateway.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],
                operation_digest=n['operation_digest'],decision='approve',
                proof_token=gateway.issue_demo_proof(n['challenge_id'],principal_id='demo_user_a')),principal_id='demo_user_a')
        assert outcome['status']==expected_status
        if expected_status=='failed': assert outcome['error']['code']=='INVALID_STATE'
        with store.connection() as c:
            state=c.execute('SELECT card_state FROM cards WHERE card_id=?',(card,)).fetchone()[0]
            balance=c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0]
            ledger=c.execute('SELECT COUNT(*) FROM ledger_entries').fetchone()[0]
        assert state==expected_state and balance==200000 and ledger==0
        record(case,dict(outcome=outcome,card_state=state,balance_minor=balance,ledger_entries=ledger))

modules=['tests.test_gateway','tests.test_recovery','tests.test_app','tests.test_split','tests.test_cards',
         'tests.test_material_access','tests.test_billing','tests.test_material_checks','tests.test_material_v12']
with TemporaryDirectory() as td, patch('bank.seed.PROJECT_ROOT',Path(td)):
    suite=unittest.TestLoader().loadTestsFromNames(modules)
    with (OUT/'2026-10-16-material-retest.txt').open('w',encoding='utf-8') as log:
        tested=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
result['existing_tests']=dict(modules=modules,run=tested.testsRun,failures=len(tested.failures),
    errors=len(tested.errors),skipped=len(tested.skipped),passed=tested.wasSuccessful() and not tested.skipped)
result['source_unchanged']=all(sha256(Path(p).read_bytes()).hexdigest()==h for p,h in source_hashes.items())
result['runtime_unchanged']=(sha256(runtime.read_bytes()).hexdigest() if runtime.exists() else None)==runtime_before
assert result['source_unchanged'] and result['runtime_unchanged']
(OUT/'2026-10-16-material-retest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(material_cases=len(result['cases']),existing_tests=result['existing_tests'],
    source_unchanged=result['source_unchanged'],runtime_unchanged=result['runtime_unchanged']),ensure_ascii=False))
raise SystemExit(0 if result['existing_tests']['passed'] else 1)
