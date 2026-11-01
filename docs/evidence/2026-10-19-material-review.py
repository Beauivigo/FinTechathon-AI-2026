"""A 独立验收：只读原始包，临时库运行，不采用成员写死的通过统计。"""
import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
SOURCE = ROOT / 'materials/inbox/2026-10-19-C'
OUT = Path(__file__).with_suffix('.json')
checks = []


def check(name, ok, actual=None):
    checks.append(dict(id=name, passed=bool(ok), actual=actual))


def verify_jac(summary, prefix):
    rows = {c['id']: c['actual'] for c in summary['cases']}
    def c(cid, predicate):
        a = rows[cid]
        check(prefix + ':' + cid, predicate(a), a)
    def bal(a, field='balance', expected=(200000, 200000, 0)):
        b = a[field]
        return tuple(b[k] for k in ('balance_minor','available_minor','reserved_minor')) == expected
    def err(a, key='error', code='INVALID_STATE'):
        return a[key]['code'] == code
    if 'JAC-1-01' in rows:
        c('JAC-1-01', lambda a: a['pending_status']=='awaiting_confirmation' and a['final_status']=='succeeded' and bal(a,'balance_after',(200000,170000,30000)) and a['reservation']['state']=='active')
        c('JAC-1-02', lambda a: a['pending_status']=='awaiting_confirmation' and a['balance_before']==a['balance_pending'] and bal(a,'balance_pending'))
        c('JAC-1-03', lambda a: a['first_status']==a['replay_status']=='succeeded' and a['first_data']==a['replay_data'] and bal(a,expected=(200000,170000,30000)))
        c('JAC-1-04', lambda a: a['release_status']=='succeeded' and set(a['release_data'])=={'reservation_id'} and bal(a,'balance_after') and a['reservation']['state']=='released')
        c('JAC-1-05', lambda a: a['second_release_status']=='failed' and err(a) and bal(a))
        c('JAC-1-06', lambda a: a['release_status']==a['replay_status']==a['query_status']==a['queried_result_status']=='succeeded' and set(a['queried_data'])=={'reservation_id'} and bal(a))
        c('JAC-1-07', lambda a: a['final_status']=='cancelled' and a['reservations_count']==0 and a['balance_before']==a['balance_after'] and bal(a,'balance_after'))
        c('JAC-1-08', lambda a: a['final_status']=='failed' and err(a,code='AUTH_EXPIRED') and bal(a))
        c('JAC-1-09', lambda a: a['final_status']=='failed' and err(a,code='FORBIDDEN'))
        c('JAC-1-10', lambda a: a['final_status']=='failed' and err(a,code='INSUFFICIENT_FUNDS') and bal(a))
        c('JAC-1-11', lambda a: a['first_status']=='succeeded' and a['second_status']=='failed' and err(a,'second_error','INSUFFICIENT_FUNDS') and bal(a,expected=(200000,80000,120000)))
        c('JAC-1-12', lambda a: a['release_a_status']=='succeeded' and a['release_b_status']=='failed' and err(a,'release_b_error') and bal(a) and a['reservation']['state']=='released')
        c('JAC-1-13', lambda a: all(a[k]['raised'] and a[k]['code']=='INVALID_REQUEST' for k in ('zero','negative')) and bal(a))
        c('JAC-1-14', lambda a: a['ledger_entries']==a['transfers']==0 and bal(a))
        c('JAC-1-15', lambda a: a['final_status']=='failed' and err(a,code='FORBIDDEN') and bal(a))
        c('JAC-1-16', lambda a: all(any(r['action']==action and r['outcome']=='succeeded' and json.loads(r['details']).get('effect')==effect and json.loads(r['details']).get('amount_minor')==30000 and json.loads(r['details']).get('posting_key') for r in a['recent_audit']) for action,effect in (('budget.reserve','reserve'),('budget.release','release_reserved'))))
        c('JAC-1-17', lambda a: a['merchant_order_probe_status']=='failed' and err(a,'merchant_order_probe_error','INVALID_REQUEST'))
    else:
        c('JAC-2-01', lambda a: a['pending_status']=='awaiting_confirmation' and a['final_status']=='succeeded' and a['created']==1 and a['subscription']['interval']=='monthly' and a['subscription']['subscription_state']=='candidate' and a['subscription']['next_charge_date']=='2026-11-05' and '3 期' in a['subscription']['evidence'])
        c('JAC-2-02', lambda a: a['final_status']=='succeeded' and a['has_new_candidate'] and a['seed_states']['SUB-E001']=='candidate' and a['seed_states']['SUB-E003']=='active')
        c('JAC-2-03', lambda a: a['first_scan_created']==1 and a['second_scan_created']==0 and a['fixed_sub_unchanged'])
        c('JAC-2-04', lambda a: a['created']==0 and a['count']==1)
        c('JAC-2-05', lambda a: a['first_status']==a['replay_status']=='succeeded' and a['same'] and a['count']==1)
        c('JAC-2-06', lambda a: a['accept_status']=='succeeded' and a['accept_data']['subscription_state']=='active' and a['second_review_status']=='failed' and err(a,'second_review_error'))
        c('JAC-2-07', lambda a: a['reject_status']=='succeeded' and a['reject_data']['subscription_state']=='dismissed')
        c('JAC-2-08', lambda a: a['final_status']=='cancelled' and a['count']==0)
        c('JAC-2-09', lambda a: a['owner_scan_status']=='succeeded' and a['owner_scan_created']==1 and a['cross_user_list_status']=='failed' and err(a,'cross_user_list_error','FORBIDDEN'))
        c('JAC-2-10', lambda a: a['created']==1 and a['before']==a['after_ledger']==a['after_scan'])
        c('JAC-2-11', lambda a: a['created']==2 and set(a['created_merchants'])=={'JAC偏差三天','JAC容差通过'})
        c('JAC-2-12', lambda a: a['created']==0)
        for cid in ('JAC-2-13','JAC-2-14'):
            c(cid, lambda a: a['gateway_status']=='failed' and err(a,code='INVALID_REQUEST'))
        c('JAC-4-01', lambda a: a['merchant_order_status']==a['scenario_plan_status']=='failed' and err(a,'merchant_order_error','INVALID_REQUEST') and err(a,'scenario_plan_error','INVALID_REQUEST'))


def runtime_hashes():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'runtime').rglob('*') if p.is_file()}


before = runtime_hashes()
summaries = {}
for path in sorted(SOURCE.glob('*.py')):
    # Scripts were read in full. run_name avoids original __main__ JSON writes;
    # top-level gateway cases create and clean their own TemporaryDirectory DBs.
    module = runpy.run_path(str(path))
    summary = module['summary']
    summaries[path.stem] = summary['cases']
    original = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    if 'jac-group' in path.name:
        verify_jac(original, 'member-json')
        verify_jac(summary, 'A-rerun')
    else:
        for source, data in [('member-json',original),('A-rerun',summary)]:
            for case in data['cases']:
                check(source+':'+case['id'], case['verdict']=='符合')
    if 'r03b-reference' in path.name:
        baseline = [module['t']('2026-08-01',1000,'debit','consumption'),module['t']('2026-08-02',1000,'debit','consumption'),module['t']('2026-08-03',1001,'debit','consumption')]
        actual, detail = module['branch_b'](10000, baseline, '2026-08-04')
        check('diagnostic:C-R03B-premature-floor-reproduced', actual=='HIT' and 10000*3 < 3001*10, dict(original=actual,detail=detail,correct='MISS',exact_threshold='30010/3'))

corrected = runpy.run_path(str(ROOT/'materials/reviewed/2026-10-19-C/r03b_reference_corrected.py'))
check('C-R03B-corrected-original-eight',corrected['summary']['fail_count']==0)
base = [corrected['t']('2026-08-01',1000,'debit','consumption'),corrected['t']('2026-08-02',1000,'debit','consumption'),corrected['t']('2026-08-03',1001,'debit','consumption')]
for amount,expected in ((10000,'MISS'),(10003,'MISS'),(10004,'HIT')):
    actual,detail=corrected['branch_b'](amount,base,'2026-08-04')
    check('C-R03B-corrected:'+str(amount),actual==expected,dict(verdict=actual,detail=detail))

# E-V3 annual tolerance against actual gateway implementation, via existing test fixture.
from tests.test_subscription_scan import SubscriptionScanTests
annual = [('+3','2025-01-10','2026-01-13',6800,6800,1),('+4','2025-01-10','2026-01-14',6800,6800,0),('-3','2025-01-10','2026-01-07',6800,6800,1),('-4','2025-01-10','2026-01-06',6800,6800,0),('leap','2024-02-29','2025-02-28',6800,6800,1),('amount5pct','2025-01-10','2026-01-13',6800,7140,1),('amount-over','2025-01-10','2026-01-13',6800,7141,0)]
for name,d1,d2,a1,a2,expected in annual:
    fixture = SubscriptionScanTests(); fixture.setUp()
    try:
        fixture.add(name,d1,a1); fixture.add(name,d2,a2)
        response = fixture.scan()
        check('E-yearly:'+name, response['status']=='succeeded' and response['data']['created']==expected and all(s['interval']=='yearly' for s in response['data']['subscriptions']),response)
    finally:
        fixture.doCleanups()

# B table arithmetic only: these are not merchant runtime tests.
for name, consumed, released, expected in [('P1',0,0,(200000,170000,30000)),('P3',18000,12000,(182000,182000,0)),('P4',30000,0,(170000,170000,0)),('P5-committed',18000,0,(182000,170000,12000)),('P6',0,30000,(200000,200000,0)),('P7-committed-after-cake-release',18000,12000,(182000,182000,0)),('P7-uncommitted-after-cake-release',0,12000,(200000,182000,18000))]:
    actual=(200000-consumed,170000+released,30000-consumed-released)
    check('B-handcalc:'+name,actual==expected and actual[0]==actual[1]+actual[2],dict(values=actual,consumed=consumed,released=released,remaining=actual[2]))
check('runtime-unchanged',before==runtime_hashes())
result = dict(executor='A/review independent rerun',date='2026-10-19',head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),python=sys.version,scope='B/C/E materials only; D excluded; reference cases are not production feature acceptance',checks=checks,rerun_cases=summaries,runtime_before=before,passed=sum(c['passed'] for c in checks),failed=sum(not c['passed'] for c in checks))
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
print(json.dumps({k:result[k] for k in ('head','passed','failed')},ensure_ascii=False))
for c in checks:
    if not c['passed']: print('FAILED',c['id'],c['actual'])
sys.exit(bool(result['failed']))
