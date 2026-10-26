"""Persistent local scheduler. No model calls and no authority to approve a payment.

The worker heartbeat and ownership live in the SQLite ``worker_lease`` row.
The UI supervisor writes the lease before spawning this process and passes the
owner token through ``FINTECH_WORKER_OWNER``.  A stop request is observed
before new work is claimed; already-started business operations are never
cancelled or marked paid by the worker.
"""
import json
import os
import secrets
import time
from datetime import datetime, timezone

from .gateway import BankGateway
from .scheduler import Scheduler
from .merchant_scheduler import MerchantScheduler
from .plan_store import PlanStore
from agent.controller import Conversation
from agent.model_bank import BankPlanRunner, NeedsInput


class Worker:
    def __init__(self, store, *, clock=None, owner=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.owner = owner or secrets.token_hex(16)

    def _lease(self, c):
        return c.execute("SELECT * FROM worker_lease WHERE name='main'").fetchone()

    def stop_requested(self):
        with self.store.connection() as c:
            row = self._lease(c)
            return bool(row and row['owner'] == self.owner and row['stop_requested'])

    def tick(self):
        now = self.clock()
        stamp = now.timestamp()
        with self.store.connection(write=True) as c:
            row = self._lease(c)
            if row and row['expires'] > stamp and row['owner'] != self.owner:
                return False
            if row and row['owner'] == self.owner and row['stop_requested']:
                c.execute("UPDATE worker_lease SET expires=0,last_tick=? WHERE name='main' AND owner=?",
                          (now.isoformat(), self.owner))
                return False
            if row is None:
                c.execute('''INSERT INTO worker_lease
                    (name,owner,expires,last_tick,error,pid,started_at,stop_requested,stop_requested_at)
                    VALUES ('main',?,?,?,NULL,?,?,0,NULL)''',
                          (self.owner, stamp + 30, now.isoformat(), os.getpid(), now.isoformat()))
            elif row['owner'] == self.owner:
                c.execute('''UPDATE worker_lease SET expires=?,last_tick=?,error=NULL,
                    pid=COALESCE(pid,?),started_at=COALESCE(started_at,?)
                    WHERE name='main' AND owner=?''',
                          (stamp + 30, now.isoformat(), os.getpid(), now.isoformat(), self.owner))
            else:
                # Take over an expired lease left by a crashed worker.
                c.execute('''UPDATE worker_lease SET owner=?,expires=?,last_tick=?,error=NULL,pid=?,
                    started_at=?,stop_requested=0,stop_requested_at=NULL
                    WHERE name='main' AND owner=?''',
                          (self.owner, stamp + 30, now.isoformat(), os.getpid(), now.isoformat(), row['owner']))
            principals = [r[0] for r in c.execute('SELECT principal_id FROM principals')]
        error = False
        stopped = False
        gateway = BankGateway(self.store, clock=self.clock, trusted_worker=True)
        journal = PlanStore(self.store)
        for principal in principals:
            if self.stop_requested():
                stopped = True
                break
            try:
                Scheduler(gateway).run_once(principal_id=principal)
                MerchantScheduler(gateway).run_once(principal_id=principal)
                gateway.run_reminders(principal_id=principal)
                for plan in journal.list(principal):
                    if self.stop_requested():
                        stopped = True
                        break
                    snap, _ = journal.load(plan['plan_id'], principal)
                    if not snap.get('automation') or snap['paused']:
                        continue
                    flow = Conversation(gateway, principal_id=principal)
                    runner = BankPlanRunner.restore(flow, journal, plan['plan_id'])
                    for _ in runner.tasks:
                        if self.stop_requested():
                            stopped = True
                            break
                        if runner.advance(allow_write=True) is None:
                            break
            except Exception:
                error = True
            if stopped:
                break
            with self.store.connection(write=True) as c:
                for row in c.execute("SELECT action,idempotency_key,result_json FROM operations WHERE principal_id=?",
                                     (principal,)).fetchall():
                    result = json.loads(row['result_json'])
                    status = result['status']
                    if status in ('awaiting_confirmation', 'awaiting_verification', 'unknown', 'failed', 'blocked', 'succeeded'):
                        key = 'operation:' + principal + ':' + row['action'] + ':' + row['idempotency_key'] + ':' + status
                        c.execute('INSERT OR IGNORE INTO notifications(notification_key,principal_id,message,created_at) VALUES (?,?,?,?)',
                                  (key, principal, row['action'] + ' · ' + status + ' · ' + row['idempotency_key'], now.isoformat()))
                for row in c.execute("SELECT reminder_id FROM subscription_reminders WHERE principal_id=? AND state='sent'",
                                     (principal,)).fetchall():
                    c.execute('INSERT OR IGNORE INTO notifications(notification_key,principal_id,message,created_at) VALUES (?,?,?,?)',
                              ('reminder:' + row[0], principal, '订阅续费提醒已到期：' + row[0], now.isoformat()))
        with self.store.connection(write=True) as c:
            message = None if not error else '部分任务需核实，请查看原操作。'
            c.execute('''UPDATE worker_lease SET last_tick=?,error=?,expires=?
                WHERE name='main' AND owner=?''',
                      (now.isoformat(), message, 0 if stopped else stamp + 30, self.owner))
        return not stopped

    def close(self):
        with self.store.connection(write=True) as c:
            c.execute("UPDATE worker_lease SET expires=0 WHERE name='main' AND owner=?", (self.owner,))


def main():
    import argparse
    from .store import Store
    from .seed import initialize, demo_path
    parser = argparse.ArgumentParser(description='本应用持久后台检查；不批准付款。')
    parser.add_argument('--once', action='store_true', help='只检查一轮后退出')
    parser.add_argument('--interval', type=float, default=5.0, help='检查间隔秒数')
    parser.add_argument('--database', help='受信任启动器指定的本机数据库路径')
    args = parser.parse_args()
    store = Store(args.database or demo_path())
    initialize(store)
    worker = Worker(store, owner=os.environ.get('FINTECH_WORKER_OWNER') or None)
    try:
        while True:
            progressed = worker.tick()
            if not progressed or args.once:
                break
            deadline = time.monotonic() + max(0.5, args.interval)
            while time.monotonic() < deadline:
                if worker.stop_requested():
                    break
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
            if worker.stop_requested():
                break
    except KeyboardInterrupt:
        pass
    finally:
        worker.close()


if __name__ == '__main__':
    main()
