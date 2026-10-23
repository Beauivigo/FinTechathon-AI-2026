"""Persistent local scheduler. No model calls and no authority to approve a payment."""
import json
import secrets
from datetime import datetime,timezone
from .gateway import BankGateway
from .scheduler import Scheduler
from .merchant_scheduler import MerchantScheduler
from .plan_store import PlanStore
from agent.controller import Conversation
from agent.model_bank import BankPlanRunner,NeedsInput

class Worker:
    def __init__(self,store,*,clock=None):
        self.store=store;self.clock=clock or (lambda:datetime.now(timezone.utc));self.owner=secrets.token_hex(16)

    def tick(self):
        now=self.clock();stamp=now.timestamp()
        with self.store.connection(write=True) as c:
            row=c.execute("SELECT * FROM worker_lease WHERE name='main'").fetchone()
            if row and row['expires']>stamp and row['owner']!=self.owner:return False
            c.execute("INSERT INTO worker_lease VALUES ('main',?,?,?,NULL) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires=excluded.expires,last_tick=excluded.last_tick,error=NULL",
                      (self.owner,stamp+30,now.isoformat()))
            principals=[r[0] for r in c.execute('SELECT principal_id FROM principals')]
        error=False
        gateway=BankGateway(self.store,clock=self.clock,trusted_worker=True)
        journal=PlanStore(self.store)
        for principal in principals:
            try:
                Scheduler(gateway).run_once(principal_id=principal)
                MerchantScheduler(gateway).run_once(principal_id=principal)
                gateway.run_reminders(principal_id=principal)
                for plan in journal.list(principal):
                    snap,_=journal.load(plan['plan_id'],principal)
                    if not snap.get('automation') or snap['paused']:continue
                    flow=Conversation(gateway,principal_id=principal)
                    runner=BankPlanRunner.restore(flow,journal,plan['plan_id'])
                    for _ in runner.tasks:
                        if runner.advance(allow_write=True) is None:break
            except Exception:error=True
            with self.store.connection(write=True) as c:
                for row in c.execute("SELECT action,idempotency_key,result_json FROM operations WHERE principal_id=?",(principal,)).fetchall():
                    result=json.loads(row['result_json']);status=result['status']
                    if status in ('awaiting_confirmation','awaiting_verification','unknown','failed','blocked','succeeded'):
                        key='operation:'+principal+':'+row['action']+':'+row['idempotency_key']+':'+status
                        c.execute('INSERT OR IGNORE INTO notifications(notification_key,principal_id,message,created_at) VALUES (?,?,?,?)',
                                  (key,principal,row['action']+' · '+status+' · '+row['idempotency_key'],now.isoformat()))
                for row in c.execute("SELECT reminder_id FROM subscription_reminders WHERE principal_id=? AND state='sent'",(principal,)).fetchall():
                    c.execute('INSERT OR IGNORE INTO notifications(notification_key,principal_id,message,created_at) VALUES (?,?,?,?)',
                              ('reminder:'+row[0],principal,'订阅续费提醒已到期：'+row[0],now.isoformat()))
        with self.store.connection(write=True) as c:
            c.execute("UPDATE worker_lease SET last_tick=?,error=? WHERE name='main' AND owner=?",(now.isoformat(),'部分任务需核实，请查看原操作。' if error else None,self.owner))
        return True

    def close(self):
        with self.store.connection(write=True) as c:c.execute("UPDATE worker_lease SET expires=0 WHERE owner=?",(self.owner,))


def main():
    import argparse,time
    from .store import Store
    from .seed import initialize,demo_path
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');args=parser.parse_args()
    store=Store(demo_path());initialize(store);worker=Worker(store)
    try:
        while True:
            worker.tick()
            if args.once:break
            time.sleep(5)
    except KeyboardInterrupt:pass
    finally:worker.close()


if __name__=='__main__':main()
