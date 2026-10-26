"""U3: in-app background start/stop, single instance, preference and stop safety.

Temporary bank only; the spawned process is a test double and no real worker,
network, runtime database or model call is used.
"""
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from bank.store import Store
from bank.seed import initialize
from bank.background import BackgroundController
from bank.worker import Worker
from bank.gateway import BankGateway
from agent.controller import Conversation

ROOT = Path(__file__).resolve().parents[1]


class FakeProcess:
    def __init__(self, pid):
        self.pid = pid
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'bank.sqlite3')
        initialize(self.store)
        self.now = [1000.0]
        self.spawned = []

    def spawn(self, owner):
        process = FakeProcess(4000 + len(self.spawned))
        self.spawned.append((owner, process))
        return process

    def controller(self, **kwargs):
        defaults = dict(clock=lambda: self.now[0], sleep=lambda seconds: None, spawn=self.spawn)
        defaults.update(kwargs)
        return BackgroundController(self.store, **defaults)

    def worker(self, owner, when=None):
        moment = datetime.fromtimestamp(self.now[0] if when is None else when, timezone.utc)
        return Worker(self.store, clock=lambda: moment, owner=owner)

    def test_start_is_idempotent_and_preference_persists(self):
        controller = self.controller()
        self.assertFalse(controller.preference())
        result = controller.ensure_started()
        self.assertTrue(result['started'])
        self.assertTrue(result['starting'])
        self.assertFalse(result['running'])
        self.worker(self.spawned[0][0]).tick()
        self.assertTrue(controller.status()['running'])
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(controller.status()['pid'], self.spawned[0][1].pid)
        for _ in range(3):
            again = controller.ensure_started()
            self.assertFalse(again['started'])
            self.assertEqual(again['reason'], 'already_running')
        self.assertEqual(len(self.spawned), 1)
        restarted = self.controller()
        self.assertTrue(restarted.preference())
        self.assertEqual(restarted.status()['running'], True)
        restarted.ensure_started(enable=False)
        self.assertEqual(len(self.spawned), 1)

    def test_concurrent_pages_cannot_spawn_two_workers(self):
        barrier = threading.Barrier(4)
        outcomes = []
        controls = [self.controller() for _ in range(4)]

        def start(control, enable):
            barrier.wait()
            outcomes.append(control.ensure_started(enable=enable))

        threads = []
        for enable, control in ((True, controls[0]), (False, controls[1]),
                                (False, controls[2]), (False, controls[3])):
            thread = threading.Thread(target=start, args=(control, enable))
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(sum(1 for result in outcomes if result['started']), 1)
        self.assertEqual(self.controller().status()['preference'], True)

    def test_restart_after_lease_expiry_restores_preference(self):
        first = self.controller()
        first.ensure_started()
        self.now[0] += 31
        restarted = self.controller()
        status = restarted.status()
        self.assertTrue(status['preference'])
        self.assertFalse(status['running'])
        restored = restarted.ensure_started(enable=False)
        self.assertTrue(restored['started'])
        self.assertEqual(len(self.spawned), 2)
        restarted.request_stop(wait=0)
        self.now[0] += 31
        disabled = restarted.ensure_started(enable=False)
        self.assertFalse(disabled['started'])
        self.assertEqual(disabled['reason'], 'disabled')
        self.assertEqual(len(self.spawned), 2)

    def test_spawn_failure_is_reported_and_retryable(self):
        def broken(owner):
            raise OSError('test environment unavailable')

        controller = self.controller(spawn=broken)
        failed = controller.ensure_started()
        self.assertFalse(failed['started'])
        self.assertEqual(failed['reason'], 'failed')
        status = controller.status()
        self.assertTrue(status['preference'])
        self.assertFalse(status['running'])
        self.assertIn('启动失败', status['last_error'])
        controller._spawn = self.spawn
        retried = controller.ensure_started(enable=False)
        self.assertTrue(retried['started'])
        self.assertEqual(len(self.spawned), 1)

    def make_scheduled_task(self):
        now = datetime.fromtimestamp(self.now[0], timezone.utc)
        gateway = BankGateway(self.store, clock=lambda: now)
        flow = Conversation(gateway, principal_id='demo_user_a')
        execute_at = (now + timedelta(minutes=2)).isoformat()
        result = flow.start_action('transfer.schedule', dict(
            account_id='demo_account_a', payee_id='demo_payee_li',
            amount_minor=20000, execute_at=execute_at))
        self.assertEqual(result['status'], 'awaiting_confirmation')
        flow.decide(True)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT state FROM tasks").fetchone()[0], 'scheduled')
        self.now[0] = (now + timedelta(minutes=3)).timestamp()

    def operation_count(self, action=None):
        with self.store.connection() as c:
            if action is None:
                return c.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
            return c.execute('SELECT COUNT(*) FROM operations WHERE action=?', (action,)).fetchone()[0]

    def test_stop_prevents_new_work_but_keeps_started_operations_for_checking(self):
        self.make_scheduled_task()
        pending = Conversation(BankGateway(self.store, clock=lambda: datetime.fromtimestamp(self.now[0], timezone.utc)),
                               principal_id='demo_user_a')
        pending.start_action('transfer.create', dict(
            account_id='demo_account_a', payee_id='demo_payee_li', amount_minor=20000))
        controller = self.controller()
        controller.ensure_started()
        owner, process = self.spawned[0]
        worker = self.worker(owner)
        before = self.operation_count('transfer.create')
        self.assertEqual(before, 1)
        stopping = controller.request_stop(wait=0)
        self.assertTrue(stopping['requested'])
        self.assertTrue(worker.stop_requested())
        self.assertFalse(worker.tick())
        self.assertEqual(self.operation_count('transfer.create'), before)
        self.assertFalse(process.terminated)  # only the DB stop flag is used
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT state FROM tasks").fetchone()[0], 'scheduled')
        self.assertFalse(controller.status()['running'])
        # A fresh app start with the preference re-enabled resumes due work.
        self.now[0] += 31
        second = self.controller()
        self.assertTrue(second.ensure_started(enable=True)['started'])
        new_owner = self.spawned[-1][0]
        self.assertTrue(self.worker(new_owner).tick())
        self.assertEqual(self.operation_count('transfer.create'), before + 1)
        self.assertEqual(self.operation_count('transfers'), 0)  # dispatch still needs user confirmation

    def test_worker_heartbeat_and_other_live_owner_is_not_displaced(self):
        controller = self.controller()
        controller.ensure_started()
        owner = self.spawned[0][0]
        first = self.worker(owner)
        self.assertTrue(first.tick())
        with self.store.connection() as c:
            row = c.execute("SELECT * FROM worker_lease WHERE name='main'").fetchone()
            self.assertEqual(row['pid'], self.spawned[0][1].pid)
            self.assertTrue(row['last_tick'])
            self.assertEqual(row['stop_requested'], 0)
        other = Worker(self.store, clock=first.clock, owner='another-owner')
        self.assertFalse(other.tick())
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT owner FROM worker_lease WHERE name='main'").fetchone()[0], owner)

    def test_stop_when_never_enabled_writes_no_state(self):
        before = self.store.path.read_bytes()
        result = self.controller().request_stop(wait=0)
        self.assertFalse(result['live'])
        self.assertTrue(result['confirmed_stopped'])
        self.assertEqual(self.store.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
