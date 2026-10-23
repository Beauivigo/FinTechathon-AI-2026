"""Independent H0-H6 acceptance; temporary banks, no real HTTP or user enrollment."""
from contextlib import redirect_stdout,redirect_stderr,closing
import argparse
from hashlib import sha256
import json,os,shutil,subprocess,sys,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def fingerprint(path):return sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def sources():
    return {str(p.relative_to(ROOT)):fingerprint(p) for folder in ('agent','app','bank','shared','tests','scripts','billing','wealth','scenario','subscription','card') for p in (ROOT/folder).rglob('*.py')}

def clean_start(out):
    with TemporaryDirectory() as td:
        clean=Path(td)
        for name in ('agent','app','bank','shared','billing','wealth','scenario','subscription','card','material_check'):
            shutil.copytree(ROOT/name,clean/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copytree(ROOT/'.streamlit',clean/'.streamlit',ignore=shutil.ignore_patterns('secrets.toml'))
        env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','USERPROFILE','APPDATA','LOCALAPPDATA','HOMEDRIVE','HOMEPATH','COMSPEC'}}
        env.update(TEMP=td,TMP=td,PYTHONIOENCODING='utf-8')
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        prepare="""from bank.store import Store
from bank.seed import initialize,demo_path
from bank.gateway import BankGateway
from bank.plan_store import PlanStore
from agent.controller import Conversation
from agent.model_bank import BankPlanRunner
s=Store(demo_path());initialize(s)
f=Conversation(BankGateway(s),principal_id='demo_user_a')
r=BankPlanRunner(f,[dict(id='reserve',title='后台测试',intent='budget_reserve',fields=dict(account_id='demo_account_a',amount_text='20元',purpose='独立启动测试'),depends_on=[])],trace_id='plan_'+'c'*32,journal=PlanStore(s))
r.automation=True;r.checkpoint()
"""
        commands=[('prepare',[sys.executable,'-B','-X','utf8','-c',prepare]),
                  ('worker_once',[sys.executable,'-B','-X','utf8','-m','bank.worker','--once']),
                  ('worker_restart',[sys.executable,'-B','-X','utf8','-m','bank.worker','--once'])]
        outcomes=[]
        for name,cmd in commands:
            result=subprocess.run(cmd,cwd=clean,env=env,capture_output=True,text=True,timeout=30,creationflags=flags)
            outcomes.append(dict(name=name,exit_code=result.returncode))
            if result.returncode:raise RuntimeError('Clean start failed: '+name+' '+result.stderr[-1500:])
        import sqlite3,time
        process=subprocess.Popen([sys.executable,'-B','-X','utf8','-m','bank.worker'],cwd=clean,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=flags)
        try:
            time.sleep(1.5)
            with closing(sqlite3.connect(clean/'runtime/sandbox.sqlite3')) as c:first=c.execute("SELECT last_tick FROM worker_lease WHERE name='main'").fetchone()[0]
            time.sleep(5.5)
            with closing(sqlite3.connect(clean/'runtime/sandbox.sqlite3')) as c:second=c.execute("SELECT last_tick FROM worker_lease WHERE name='main'").fetchone()[0]
            if first==second or process.poll() is not None:raise RuntimeError('Background loop did not advance heartbeat')
            outcomes.append(dict(name='worker_loop_without_page',exit_code=0))
        finally:
            if process.poll() is None:process.terminate()
            process.wait(timeout=10)
            if process.stderr:process.stderr.close()
        check="""from bank.store import Store
from bank.seed import demo_path
s=Store(demo_path())
with s.connection() as c:
 assert c.execute('SELECT COUNT(*) FROM operations').fetchone()[0]==1
 assert c.execute('SELECT COUNT(*) FROM reservations').fetchone()[0]==0
 assert c.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]==1
 assert c.execute(\"SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'\").fetchone()[0]==200000
from streamlit.testing.v1 import AppTest
from pathlib import Path
a=AppTest.from_file(str(Path.cwd()/'app/main.py'),default_timeout=15).run()
assert not a.exception
"""
        result=subprocess.run([sys.executable,'-B','-X','utf8','-c',check],cwd=clean,env=env,capture_output=True,text=True,timeout=45,creationflags=flags)
        outcomes.append(dict(name='ledger_and_clean_page',exit_code=result.returncode))
        (out/'clean-page.txt').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        if result.returncode:raise RuntimeError('Clean page failed; see clean-page.txt')
        return outcomes

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='docs/evidence/h0-h6-20261023');parser.add_argument('--clean-only',action='store_true');args=parser.parse_args()
    out=ROOT/args.output;out.mkdir(parents=True,exist_ok=False)
    if args.clean_only:
        print(json.dumps(clean_start(out)));return 0
    runtime=ROOT/'runtime/sandbox.sqlite3';profile=ROOT/'.cache/bank-model/profile.json'
    before=fingerprint(runtime),fingerprint(profile);baseline=sources()
    with (out/'full.txt').open('w',encoding='utf-8') as log,redirect_stdout(log),redirect_stderr(log),TemporaryDirectory() as td,patch('bank.seed.PROJECT_ROOT',Path(td)),patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('Offline acceptance forbids real HTTP')):
        suite=unittest.TestLoader().discover(str(ROOT/'tests'))
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    clean_error=None
    try:clean=clean_start(out) if result.wasSuccessful() else []
    except Exception as exc:clean=[];clean_error=str(exc)
    record=dict(executor='Codex independent acceptance',command='python -B -X utf8 scripts/verify_h_core.py',run=result.testsRun,
                failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),source_sha256=baseline,
                source_unchanged=baseline==sources(),runtime_unchanged=fingerprint(runtime)==before[0],profile_unchanged=fingerprint(profile)==before[1],
                clean_start=clean,clean_error=clean_error,real_http_blocked=True,phone_enrollment='Not performed by user; automated temporary enrollment is covered separately',
                scope='Local simulated finance; offline model mocks; Windows DPAPI and temporary TOTP fixtures. No live bank, user phone, paid model call or deployment.')
    record['passed']=bool(result.wasSuccessful() and not result.skipped and all(record[k] for k in ('source_unchanged','runtime_unchanged','profile_unchanged')) and clean)
    (out/'full.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in record.items() if k!='source_sha256'},ensure_ascii=False))
    return 0 if record['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
