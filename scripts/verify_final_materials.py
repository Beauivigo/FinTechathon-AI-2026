"""A最后轮资金资料补测：独立模拟库，不更改成员原件或日常库，不调用模型。"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared import ServiceError
from wealth.catalog import catalog
from tests.test_six_scenarios import SixScenarioTests

OUT = ROOT / 'docs/evidence/2026-10-23-final-material-probes.json'
P = 'demo_user_a'
A = 'demo_account_a'


@contextmanager
def fixture(amount=1000000):
    t = SixScenarioTests()
    t.setUp()
    try:
        with t.store.connection(write=True) as c:
            c.execute('UPDATE accounts SET opening_balance_minor=?,balance_minor=?,available_minor=?,reserved_minor=0 WHERE account_id=?', (amount, amount, amount, A))
        yield t
    finally:
        t.doCleanups()


def profile(t, scores):
    return t.call('wealth.assess', dict(questionnaire_version='wpq_v1',
        answers={f'q{i}':f'q{i}_{"abcd"[s-1]}' for i,s in enumerate(scores,1)}))['data']


def buy(t, product, amount, risk):
    result = t.call('wealth.purchase', dict(account_id=A,product_id=product,
                    risk_profile_id=risk['risk_profile_id'],amount_minor=amount))
    assert result['status']=='succeeded', result
    with t.store.connection() as c:
        return dict(c.execute('SELECT * FROM holdings WHERE product_id=? ORDER BY rowid DESC',(product,)).fetchone())


def redeem(t, holding, units):
    return t.call('wealth.redeem',dict(account_id=A,holding_id=holding['holding_id'],units=units))


def config():
    expected = [(100,0,100,0,0),(10000,7,100,10,0),(50000,30,125,50,25),
                (100000,90,200,100,50),(200000,180,50,150,75),(100000,60,100,80,40)]
    rows=[]
    for i,values in enumerate(expected,1):
        pid=f'WP-C{i:02d}'; product=catalog()[pid]
        actual=tuple(product[k] for k in ('minimum_amount_minor','lock_days','net_value_minor','purchase_fee_bps','redeem_fee_bps'))
        assert actual==values,(pid,actual,values)
        assert product['risk_level']==['R1','R2','R3','R4','R5','R3'][i-1]
        rows.append({'product_id':pid,'values':actual,'risk_level':product['risk_level']})
    return rows


def score_and_limits():
    with fixture() as t:
        values=[((1,1,1,1,1),'R1'),((4,4,4,4,4),'R5'),((3,3,3,2,2),'R3'),((3,3,3,3,4),'R3'),((4,4,3,3,3),'R4')]
        for answers,expected in values: assert profile(t,answers)['risk_level']==expected
        for answers in ({'q1':'q1_a'},{f'q{i}':'bad' for i in range(1,6)}):
            r=t.call('wealth.assess',dict(questionnaire_version='wpq_v1',answers=answers))
            assert r['error']['code']=='INVALID_REQUEST'
        r2=profile(t,(2,2,1,2,2))
        for product,amount,code in [('WP-C03',50000,'RISK_BLOCKED'),('WP-C02',9999,'LIMIT_EXCEEDED')]:
            r=t.call('wealth.purchase',dict(account_id=A,product_id=product,risk_profile_id=r2['risk_profile_id'],amount_minor=amount))
            assert r['error']['code']==code
        buy(t,'WP-C02',10000,r2)
        assert t.balance()==(990000,990000,0)
        return {'score_boundaries':5,'invalid_answers':2,'risk_and_minimum_rejections':2,'minimum_purchase_units':'99.9000'}


def continuous_ledger():
    # A新建算例，不能冒充缺失的D第四轮原件。费用1051，不采信无明细的3301。
    with fixture() as t:
        risk=profile(t,(4,4,4,4,4))
        h2=buy(t,'WP-C02',50000,risk);h3=buy(t,'WP-C03',200000,risk)
        assert h2['units']=='499.5000' and h3['units']=='1592.0000'
        t.now+=timedelta(days=7)
        assert redeem(t,h2,'200')['status']=='succeeded'
        assert redeem(t,h3,'1.6')['error']['code']=='INVALID_STATE'
        t.now+=timedelta(days=23)
        assert redeem(t,h3,'1.6')['status']=='succeeded'
        with t.store.connection() as c:
            orders=[dict(r) for r in c.execute('SELECT order_type,product_id,amount_minor,fee_minor,units FROM wealth_orders ORDER BY rowid')]
            ledger=[dict(r) for r in c.execute("SELECT direction,amount_minor,kind,occurred_at FROM ledger_entries WHERE kind='wealth' ORDER BY rowid")]
        assert sum(r['fee_minor'] for r in orders)==1051
        assert orders[-1]['amount_minor']==200 and orders[-1]['fee_minor']==1
        assert ledger[-1]['amount_minor']==199
        assert t.balance()==(770199,770199,0)
        return {'opening_minor':1000000,'closing':t.balance(),'fee_total_minor':1051,'orders':orders,'ledger':ledger,
                'settlement':'模拟强验证后同事务即时展示入账；不是实际等待T+1的队列。'}


def short_month_and_clear():
    with fixture() as t:
        t.now=datetime(2026,2,28,4,tzinfo=timezone.utc)
        risk=profile(t,(4,4,4,4,4));h=buy(t,'WP-C03',50000,risk)
        t.now=datetime(2026,3,29,4,tzinfo=timezone.utc)
        assert redeem(t,h,'1')['error']['code']=='INVALID_STATE'
        t.now=datetime(2026,3,30,4,tzinfo=timezone.utc)
        assert redeem(t,h,'1')['status']=='succeeded'
        cash=buy(t,'WP-C01',10000,risk)
        assert redeem(t,cash,'100')['status']=='succeeded'
        assert redeem(t,cash,'1')['error']['code']=='INVALID_STATE'
        return {'confirmed_on':'2026-02-28','unlock_on':'2026-03-30','clear_then_redeem':'INVALID_STATE'}


def large_precision_and_replay():
    with fixture(2000000) as t:
        risk=profile(t,(4,4,4,4,4))
        request=t.req('wealth.purchase',dict(account_id=A,product_id='WP-C02',risk_profile_id=risk['risk_profile_id'],amount_minor=1000001))
        done=t.finish(t.g.execute(request,principal_id=P))
        assert done['status']=='succeeded'
        assert t.g.execute(request,principal_id=P)==done
        with t.store.connection() as c:
            order=dict(c.execute('SELECT fee_minor,units FROM wealth_orders').fetchone())
            holding=c.execute('SELECT units FROM holdings').fetchone()[0]
            assert c.execute('SELECT count(*) FROM wealth_orders').fetchone()[0]==1
        assert order=={'fee_minor':1000,'units':'9990.01'} and holding=='9990.0100', (order,holding)
        assert t.balance()==(999999,999999,0)
        return {'fee_minor':1000,'holding_units':holding,'order_count':1,'balance':t.balance()}


def unknown_redeem():
    with fixture() as t:
        cash=buy(t,'WP-C01',10000,profile(t,(1,1,1,1,1)))
        request=t.req('wealth.redeem',dict(account_id=A,holding_id=cash['holding_id'],units='1'))
        pending=t.g.execute(request,principal_id=P);original=t.store.connection
        n=pending['next_action']
        proof=t.g.issue_demo_proof(n['challenge_id'],principal_id=P)
        authorization=dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve',proof_token=proof)
        @contextmanager
        def lost_reply(*,write=False):
            with original(write=write) as c:yield c
            if write:raise sqlite3.OperationalError('模拟提交后回执丢失')
        try:
            with patch.object(t.store,'connection',lost_reply):t.g.authorize(authorization,principal_id=P)
        except ServiceError as exc: assert exc.code=='RESULT_UNKNOWN', exc.code
        else:raise AssertionError('应观察到结果未知')
        query=t.req('operation.get',dict(original_action='wealth.redeem',original_idempotency_key=request['idempotency_key']))
        restored=t.g.execute(query,principal_id=P)['data']['operation_result']
        assert restored['status']=='succeeded'
        assert t.g.execute(request,principal_id=P)['data']['order_id']==restored['data']['order_id']
        with t.store.connection() as c:assert c.execute("SELECT count(*) FROM wealth_orders WHERE order_type='redeem'").fetchone()[0]==1
        assert t.balance()==(990100,990100,0)
        return {'observed':'RESULT_UNKNOWN','recovered':'succeeded','redeem_order_count':1,'balance':t.balance()}


def main():
    temp_root=ROOT/'.cache/final-material-temp'
    temp_root.mkdir(parents=True,exist_ok=True)
    tempfile.tempdir=str(temp_root)
    runtime=ROOT/'runtime/sandbox.sqlite3';before=sha256(runtime.read_bytes()).hexdigest()
    records=[]
    for function in (config,score_and_limits,continuous_ledger,short_month_and_clear,large_precision_and_replay,unknown_redeem):
        try: records.append({'name':function.__name__,'passed':True,'actual':function()})
        except Exception as exc:records.append({'name':function.__name__,'passed':False,'error':str(exc)})
    record={'executor':'A自动补测，非D原始测试','data':'虚构模拟数据，每组独立临时库','cases':records,
            'runtime_unchanged':before==sha256(runtime.read_bytes()).hexdigest(),'scope':'D既定规则与新建模拟算例；不实现真实T+1、收益或净值波动。'}
    record['passed']=all(r['passed'] for r in records) and record['runtime_unchanged']
    OUT.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':record['passed'],'cases':len(records),'failures':[r for r in records if not r['passed']],'runtime_unchanged':record['runtime_unchanged']},ensure_ascii=False))
    return 0 if record['passed'] else 1


if __name__=='__main__':sys.exit(main())
