"""U4: launcher single-instance/port/exit safety and persistent-state preservation.

Temporary directories only.  No real Streamlit, network listener, worker
process, runtime database or browser is started.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bank.store import Store
from bank.seed import initialize
from bank.lifecycle import exit_summary, normal_exit
import launcher

ROOT = Path(__file__).resolve().parents[1]


class Child:
    def __init__(self, pid=4321, code=0, running=True):
        self.pid = pid
        self.code = code
        self.running = running
        self.terminated = False

    def poll(self):
        return None if self.running else self.code

    def wait(self, timeout=None):
        self.running = False
        return self.code

    def terminate(self):
        self.terminated = True
        self.running = False

    def kill(self):
        self.terminated = True
        self.running = False


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'runtime').mkdir()
        (self.root / 'app').mkdir()
        (self.root / 'app' / 'main.py').write_text('# fixture\n', encoding='utf-8')

    def make(self, **kwargs):
        options = dict(root=self.root, check_environment=lambda: None, probe=lambda: True,
                       port_busy=lambda: False, pid_alive=lambda pid: False,
                       open_browser=lambda: None, stop_background=lambda: None,
                       sleep=lambda seconds: None, startup_timeout=1.0)
        options.update(kwargs)
        return launcher.Launcher(**options)

    def test_reuses_existing_application_without_second_service(self):
        opened = []
        stopped = []
        l = self.make(pid_alive=lambda pid: True, spawn=lambda: self.fail('must not spawn'),
                      open_browser=lambda: opened.append(True), stop_background=lambda: stopped.append(True))
        l.write_record(dict(app_pid=1234, launcher_pid=55, port=8501))
        self.assertEqual(l.run(), 0)
        self.assertEqual(opened, [True])
        self.assertEqual(stopped, [])

    def test_port_busy_never_starts_second_service_or_changes_data(self):
        database = self.root / 'runtime' / 'sandbox.sqlite3'
        database.write_bytes(b'fixture-database-bytes')
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        opened, stopped = [], []
        l = self.make(port_busy=lambda: True, spawn=lambda: self.fail('must not spawn'),
                      open_browser=lambda: opened.append(True), stop_background=lambda: stopped.append(True))
        self.assertEqual(l.run(), 2)
        self.assertEqual(opened, [])
        self.assertEqual(stopped, [])
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)

    def test_missing_dependency_has_readable_failure_without_spawn(self):
        l = self.make(check_environment=lambda: '缺少页面依赖', spawn=lambda: self.fail('must not spawn'))
        self.assertEqual(l.run(), 4)
        self.assertFalse(l.record_path.exists())

    def test_fresh_start_then_normal_exit_stops_own_background(self):
        child = Child(pid=777, code=0)
        spawned, opened, stopped = [], [], []
        l = self.make(spawn=lambda: (spawned.append(True) or child),
                      open_browser=lambda: opened.append(True),
                      stop_background=lambda: stopped.append(True),
                      wait_child=lambda process: 0)
        self.assertEqual(l.run(), 0)
        self.assertEqual(spawned, [True])
        self.assertEqual(opened, [True])
        self.assertEqual(stopped, [True])
        self.assertFalse(l.record_path.exists())

    def test_startup_failure_and_abnormal_exit_keep_data(self):
        database = self.root / 'runtime' / 'sandbox.sqlite3'
        database.write_bytes(b'keep-me')
        before = database.read_bytes()
        stopped = []
        child = Child(pid=888, code=1, running=False)
        failed = self.make(spawn=lambda: child, probe=lambda: False,
                           stop_background=lambda: stopped.append(True))
        self.assertEqual(failed.run(), 3)
        self.assertTrue(stopped)
        self.assertEqual(database.read_bytes(), before)
        self.assertFalse(failed.record_path.exists())

        healthy = Child(pid=889, code=9)
        exited = self.make(spawn=lambda: healthy, open_browser=lambda: None,
                           stop_background=lambda: stopped.append(True),
                           wait_child=lambda process: 9)
        self.assertEqual(exited.run(), 9)
        self.assertEqual(database.read_bytes(), before)
        self.assertFalse(exited.record_path.exists())

    def test_streamlit_command_is_loopback_and_not_a_second_port(self):
        command = launcher.streamlit_command('C:/python.exe', 8502, self.root)
        self.assertIn('--server.address', command)
        self.assertEqual(command[command.index('--server.address') + 1], '127.0.0.1')
        self.assertEqual(command[command.index('--server.port') + 1], '8502')
        self.assertEqual(command[command.index('--server.headless') + 1], 'true')
        self.assertIn(str(self.root / 'app' / 'main.py'), command)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'bank.sqlite3')
        initialize(self.store)

    def test_exit_summary_and_normal_exit_preserve_business_state(self):
        with self.store.connection(write=True) as c:
            c.execute('INSERT INTO operations VALUES (?,?,?,?,?,?)', (
                'demo_user_a', 'transfer.create', 'op_key', 'digest', '{}',
                json.dumps(dict(status='awaiting_verification'))))
            c.execute('INSERT INTO agent_plans VALUES (?,?,?,?,?)', (
                'plan_1', 'demo_user_a', 1,
                json.dumps(dict(tasks=[dict(id='t1', title='转账', status='pending')])), '2027-01-01'))
            c.execute('INSERT INTO notifications VALUES (?,?,?,?,0)',
                      ('notice_1', 'demo_user_a', '待核实', '2027-01-01'))
        summary = exit_summary(self.store, 'demo_user_a')
        self.assertEqual(len(summary['pending_operations']), 1)
        self.assertEqual(len(summary['pending_plans']), 1)
        self.assertEqual(summary['unread_notifications'], 1)
        self.assertFalse(summary['background']['running'])

        class Controller:
            def __init__(self):
                self.calls = 0

            def request_stop(self, *, wait=0):
                self.calls += 1
                return dict(live=False, requested=False, confirmed_stopped=True)

        controller = Controller()
        result = normal_exit(self.store, 'demo_user_a', timeout=0, controller=controller)
        self.assertEqual(controller.calls, 1)
        self.assertTrue(result['app_exit_allowed'])
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM operations').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM agent_plans').fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0], 200000)

        class StillRunning:
            def request_stop(self, *, wait=0):
                return dict(live=True, requested=True, confirmed_stopped=False)

        blocked = normal_exit(self.store, 'demo_user_a', timeout=0, controller=StillRunning())
        self.assertFalse(blocked['app_exit_allowed'])


class SyntaxTests(unittest.TestCase):
    def test_changed_python_sources_compile_without_page_dependencies(self):
        files = ('app/auth_ui.py', 'app/background_panel.py', 'app/lifecycle_panel.py',
                 'app/main.py', 'app/workflow.py', 'bank/auth.py', 'bank/background.py',
                 'bank/store.py', 'bank/worker.py', 'bank/lifecycle.py', 'launcher.py',
                 '启动银行助手.py')
        for name in files:
            with self.subTest(name=name):
                compile((ROOT / name).read_text(encoding='utf-8'), name, 'exec')


if __name__ == '__main__':
    unittest.main(verbosity=2)
