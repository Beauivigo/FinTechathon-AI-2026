"""Persistent local scheduler. No model calls and no authority to approve a payment.

The worker heartbeat and ownership live in the SQLite ``worker_lease`` row.
The UI supervisor writes the lease before spawning this process and passes the
owner token through ``FINTECH_WORKER_OWNER``.  A stop request is observed
before new work is claimed; already-started business operations are never
cancelled or marked paid by the worker.

Lease fencing: every business step first renews the lease under the owner
token.  When another instance has taken the lease over, the heartbeat update is
rejected or the row belongs to another owner, so the stale process stops
claiming work immediately and records that it has been fenced.
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

LEASE_SECONDS = 30


class _LeaseLost(Exception):
    """Raised inside a tick when this instance no longer owns the lease."""

    def __init__(self, state):
        super().__init__(state)
        self.state = state


class Worker:
    def __init__(self, store, *, clock=None, owner=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.owner = owner or secrets.token_hex(16)
        self._fenced = False

    def _lease(self, c):
        return c.execute("SELECT * FROM worker_lease WHERE name='main'").fetchone()

    @staticmethod
    def _event(c, event, stamp, detail=None):
        c.execute('INSERT INTO background_events(occurred_at,event,detail) VALUES (?,?,?)',
                  (stamp, event, detail))

    def lease_state(self):
        try:
            with self.store.connection() as c:
                row = self._lease(c)
        except Exception:
            return 'unknown'
        if row is None:
            return 'missing'
        if row['owner'] != self.owner:
            return 'lost'
        if row['expires'] <= self.clock().timestamp():
            return 'expired'
        if row['stop_requested']:
            return 'stopping'
        return 'owned'

    def stop_requested(self):
        """True when this instance must not claim more work (stop or lost lease)."""
        return self.lease_state() in {'stopping', 'expired', 'lost', 'missing', 'unknown'}

    def _may_progress(self):
        try:
            return self.lease_state() == 'owned'
        except Exception:
            return False

    def _fence(self):
        """Best-effort proof that a superseded instance is no longer progressing."""
        if self._fenced:
            return
        self._fenced = True
        try:
            with self.store.connection(write=True) as c:
                self._event(c, 'fenced', self.clock().isoformat(), json.dumps({'owner': self.owner}))
        except Exception:
            pass

    def _acknowledge_stop(self):
        now = self.clock()
        try:
            with self.store.connection(write=True) as c:
                row = self._lease(c)
                if row is None or row['owner'] != self.owner or not row['stop_requested']:
                    return False
                c.execute("UPDATE worker_lease SET expires=0,last_tick='stopped',error=NULL "
                          "WHERE name='main' AND owner=?", (self.owner,))
                self._event(c, 'worker_stopped', now.isoformat(), json.dumps({'owner': self.owner}))
            return True
        except Exception:
            return False

    def _heartbeat(self):
        """Renew this instance's lease; 'lost' when another owner took it over."""
        now = self.clock()
        stamp = now.timestamp()
        try:
            with self.store.connection(write=True) as c:
                row = self._lease(c)
                if row is None or row['owner'] != self.owner:
                    return 'lost'
                if row['stop_requested']:
                    return 'stopping'
                c.execute("UPDATE worker_lease SET expires=?,last_tick=? WHERE name='main' AND owner=?",
                          (stamp + LEASE_SECONDS, now.isoformat(), self.owner))
            return 'ok'
        except Exception:
            return 'lost'

    def _require(self):
        state = self._heartbeat()
        if state != 'ok':
            raise _LeaseLost(state)

    def tick(self):
        now = self.clock()
        stamp = now.timestamp()
        with self.store.connection(write=True) as c:
            row = self._lease(c)
            if row is not None and row['owner'] != self.owner:
                if row['expires'] > stamp:
                    return False
                if row['stop_requested']:
                    # A generation that was asked to stop is never revived by an
                    # old process; only the trusted supervisor may start a new one.
                    return False
                clean = row['last_tick'] in ('stopped', 'exited')
                c.execute('''UPDATE worker_lease SET owner=?,expires=?,last_tick=?,error=NULL,pid=?,
                    started_at=?,stop_requested=0,stop_requested_at=NULL
                    WHERE name='main' AND owner=?''',
                          (self.owner, stamp + LEASE_SECONDS, now.isoformat(), os.getpid(),
                           now.isoformat(), row['owner']))
                self._event(c, 'takeover', now.isoformat(),
                            json.dumps({'previous': row['owner'], 'clean': clean}))
            elif row is None:
                c.execute('''INSERT INTO worker_lease
                    (name,owner,expires,last_tick,error,pid,started_at,stop_requested,stop_requested_at)
                    VALUES ('main',?,?,?,NULL,?,?,0,NULL)''',
                          (self.owner, stamp + LEASE_SECONDS, now.isoformat(), os.getpid(), now.isoformat()))
            elif row['stop_requested']:
                c.execute("UPDATE worker_lease SET expires=0,last_tick='stopped',error=NULL "
                          "WHERE name='main' AND owner=?", (self.owner,))
                self._event(c, 'worker_stopped', now.isoformat(), json.dumps({'owner': self.owner}))
                return False
            else:
                c.execute('''UPDATE worker_lease SET expires=?,last_tick=?,error=NULL,
                    pid=COALESCE(pid,?),started_at=COALESCE(started_at,?)
                    WHERE name='main' AND owner=?''',
                          (stamp + LEASE_SECONDS, now.isoformat(), os.getpid(), now.isoformat(), self.owner))
            principals = [r[0] for r in c.execute('SELECT principal_id FROM principals')]
        error = False
        lost_state = None
        gateway = BankGateway(self.store, clock=self.clock, trusted_worker=True)
        journal = PlanStore(self.store)
        for principal in principals:
            try:
                self._require()
                Scheduler(gateway, guard=self._may_progress).run_once(principal_id=principal)
                self._require()
                MerchantScheduler(gateway, guard=self._may_progress).run_once(principal_id=principal)
                self._require()
                gateway.run_reminders(principal_id=principal)
                for plan in journal.list(principal):
                    self._require()
                    snap, _ = journal.load(plan['plan_id'], principal)
                    if not snap.get('automation') or snap['paused']:
                        continue
                    flow = Conversation(gateway, principal_id=principal)
                    runner = BankPlanRunner.restore(flow, journal, plan['plan_id'])
                    for _ in runner.tasks:
                        self._require()
                        if runner.advance(allow_write=True) is None:
                            break
            except _LeaseLost as lost:
                lost_state = lost.state
                break
            except Exception:
                error = True
            if not self._may_progress():
                lost_state = 'lost'
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
        if lost_state is not None:
            if lost_state == 'stopping':
                self._acknowledge_stop()
            else:
                self._fence()
            return False
        with self.store.connection(write=True) as c:
            end = self.clock()
            message = None if not error else '部分任务需核实，请查看原操作。'
            c.execute('''UPDATE worker_lease SET last_tick=?,error=?,expires=?
                WHERE name='main' AND owner=?''',
                      (end.isoformat(), message, end.timestamp() + LEASE_SECONDS, self.owner))
        return True

    def close(self):
        try:
            with self.store.connection(write=True) as c:
                row = self._lease(c)
                if row is None:
                    return
                now = self.clock().isoformat()
                if row['owner'] != self.owner:
                    self._event(c, 'fenced', now, json.dumps({'owner': self.owner}))
                    return
                if row['stop_requested']:
                    c.execute("UPDATE worker_lease SET expires=0,last_tick='stopped',error=NULL "
                              "WHERE name='main' AND owner=?", (self.owner,))
                    self._event(c, 'worker_stopped', now, json.dumps({'owner': self.owner}))
                else:
                    c.execute("UPDATE worker_lease SET expires=0,last_tick='exited' "
                              "WHERE name='main' AND owner=?", (self.owner,))
        except Exception:
            pass


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
