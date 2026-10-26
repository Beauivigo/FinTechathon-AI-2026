"""Reproduce workflow defects in temporary data. No model/network/production writes."""
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from unittest.mock import patch
from threading import Thread, Event, current_thread
import json, sys, hashlib, traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from bank.store import Store
from bank.seed import initialize
from bank.gateway import BankGateway
from bank.birthday import create_plan
from bank.plan_store import PlanStore
from bank.worker import Worker
from bank.background import BackgroundController
from bank.scheduler import Scheduler
from agent.controller import Conversation
from agent.model_bank import BankPlanRunner
from agent.model_dialogue import DialogueSession

def count(store, table):
    with store.connection() as c: return c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

def timing_revision():
    with TemporaryDirectory(dir=ROOT/'.cache') as td:
        store=Store(Path(td)/'bank.sqlite3'); initialize(store)
        now=datetime(2026,10,26,3,tzinfo=timezone.utc)
        gateway=BankGateway(store,clock=lambda:now)
        flow=Conversation(gateway,principal_id='demo_user_a')
        plan_id=create_plan(flow,birthday=date(2026,11,20),budget_minor=100000,
            account_id='demo_account_a',item_ids=['ITEM-BD-FLOWER-01'],address_id='ADDR-BD-01',
            budget_at=datetime(2026,11,1,1,tzinfo=timezone.utc),purchase_at=datetime(2026,11,18,1,tzinfo=timezone.utc))
        journal=PlanStore(store); old=BankPlanRunner.restore(flow,journal,plan_id)
        before_times={t['id']:t.get('not_before') for t in old.tasks}
        worker=Worker(store,clock=lambda:now); worker.tick()
        assert count(store,'operations')==0
        # Same accepted projection used by the model page for an edit to the purpose.
        tasks=[dict(id=t['id'],title=t['title'],intent=t['intent'],slots=dict(t['fields']),depends_on=t['depends_on']) for t in old.tasks]
        tasks[0]['slots']['purpose']='爱人生日预算'
        session=DialogueSession(lambda messages: json.dumps(dict(action='plan',tasks=tasks),ensure_ascii=False))
        snapshot=session.submit('保留原时间和步骤，只把预算用途改为爱人生日预算')
        assert snapshot['phase']=='plan'
        candidate=BankPlanRunner(flow,snapshot['proposal']['tasks'],trace_id=plan_id,journal=journal)
        candidate.inherit_completed(old); candidate.checkpoint('revision')
        after_times={t['id']:t.get('not_before') for t in candidate.tasks}
        worker.tick(); worker.close()
        with store.connection() as c:
            states=[json.loads(r[0])['status'] for r in c.execute('SELECT result_json FROM operations')]
            balance=c.execute("SELECT balance_minor,available_minor,reserved_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()
        return dict(reproduced=bool(any(before_times.values()) and not any(after_times.values()) and states==['awaiting_confirmation']),
            before_times=before_times,after_times=after_times,operation_states=states,balance=list(balance),
            scope='Protocol/compiler/journal/worker chain; deterministic model response, no real model or actual Streamlit page.')

def expired_worker_after_stop():
    with TemporaryDirectory(dir=ROOT/'.cache') as td:
        store=Store(Path(td)/'bank.sqlite3');initialize(store)
        now=[datetime(2026,10,26,3,tzinfo=timezone.utc)]
        first=Worker(store,clock=lambda:now[0],owner='first-audit-worker')
        second=Worker(store,clock=lambda:now[0],owner='second-audit-worker')
        entered,release=Event(),Event(); errors=[]
        real=Scheduler.run_once
        def delayed(self,**kwargs):
            if current_thread().name=='audit-delayed-worker':
                entered.set()
                if not release.wait(10): raise RuntimeError('probe barrier timeout')
            return real(self,**kwargs)
        def run_first():
            try:first.tick()
            except BaseException:errors.append(traceback.format_exc())
        with patch.object(Scheduler,'run_once',delayed):
            thread=Thread(target=run_first,name='audit-delayed-worker'); thread.start()
            try:
                assert entered.wait(5)
                now[0]+=timedelta(seconds=31)
                assert second.tick()  # lease expires while first worker is still alive
                controller=BackgroundController(store,clock=lambda:now[0].timestamp())
                controller.request_stop()
                assert not second.tick()  # new owner acknowledges stop
                stopped=controller.request_stop()
                assert stopped['confirmed_stopped']
                flow=Conversation(BankGateway(store,clock=lambda:now[0]),principal_id='demo_user_a')
                runner=BankPlanRunner(flow,[dict(id='reserve',title='停止后登记的待办',intent='budget_reserve',
                    fields=dict(account_id='demo_account_a',amount_text='20元',purpose='审计临时预算'),depends_on=[])],
                    trace_id='plan_'+'d'*32,journal=PlanStore(store))
                runner.automation=True;runner.checkpoint()
                before=count(store,'operations')
            finally:
                release.set();thread.join(8)
            assert not thread.is_alive() and not errors,errors
        after=count(store,'operations')
        status=controller.status()
        with store.connection() as c:
            balance=list(c.execute("SELECT balance_minor,available_minor,reserved_minor FROM accounts WHERE account_id='demo_account_a'").fetchone())
        first.close();second.close()
        return dict(reproduced=bool(stopped['confirmed_stopped'] and before==0 and after==1 and not status['live']),
            confirmed_stopped_before_resume=stopped['confirmed_stopped'],operations_before=before,operations_after=after,
            reported_running_after=status['running'],balance=balance,
            scope='Real SQLite and two worker instances in threads; deterministic long tick/lease expiry, no process kill or live service.')

if __name__=='__main__':
    out=Path(__file__).parent
    paths=[ROOT/'runtime/sandbox.sqlite3',ROOT/'.cache/bank-model/profile.json']
    def hashes():return [hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in paths]
    before=hashes()
    with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('offline only')):
        result=dict(timing_revision=timing_revision(),expired_worker_after_stop=expired_worker_after_stop())
    result['daily_data_and_profile_unchanged']=before==hashes()
    result['note']='Reproduced=true means defect exists, not repaired or accepted.'
    (out/'reproduction.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    assert all(result[k]['reproduced'] for k in ('timing_revision','expired_worker_after_stop'))
    assert result['daily_data_and_profile_unchanged']
