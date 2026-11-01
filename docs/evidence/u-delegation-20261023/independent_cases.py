"""review independent checks against the complete isolated review fixture."""
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json, secrets, sys, time, unittest, os
import socket, subprocess, urllib.request

ROOT = Path(os.environ.get('FINTECH_REVIEW_ROOT', Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(ROOT))
import pyotp
import streamlit as st
from streamlit.testing.v1 import AppTest
from bank.auth import AuthService
from bank.background import BackgroundController
from bank.gateway import BankGateway
from bank.lifecycle import normal_exit
from bank.seed import initialize
from bank.store import Store


class UserPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'bank.sqlite3')
        initialize(self.store)
        self.now = [1900000000.0]
        self.auth = AuthService(self.store, clock=lambda: self.now[0])
        self.username = 'temporary-user'
        self.password = secrets.token_urlsafe(24)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('bank.seed.PROJECT_ROOT', Path(self.temp.name)))
        self.stack.enter_context(patch('app.auth_ui.service', return_value=self.auth))
        self.stack.enter_context(patch('app.auth_ui._setup_allowed', return_value=True))
        def gateway():
            return BankGateway(self.store, auth=self.auth,
                clock=lambda: datetime.fromtimestamp(self.now[0], timezone.utc),
                session_token=st.session_state.get('auth_token'))
        self.stack.enter_context(patch('app.demo.gateway', side_effect=gateway))
        self.page = self.fresh()

    def fresh(self):
        page = AppTest.from_file(str(ROOT / 'app/main.py'), default_timeout=20).run()
        self.assertFalse(page.exception)
        return page

    def fill(self, label, value):
        next(widget for widget in self.page.text_input if widget.label == label).set_value(value)

    def press(self, label):
        next(button for button in self.page.button if button.label == label).click().run()
        self.assertFalse(self.page.exception)

    def begin(self):
        self.fill('设置用户名', self.username)
        self.fill('设置密码（至少12字符）', self.password)
        self.fill('再次输入密码', self.password)
        self.press('创建本机账号并生成恢复码')
        token = self.page.session_state['auth_token']
        return token, self.auth.pending_recovery(token)

    def acknowledge_and_bind(self):
        next(box for box in self.page.checkbox if '离线保存' in box.label).check()
        self.press('我已保存，继续绑定手机')
        token = self.page.session_state['auth_token']
        otp = pyotp.parse_uri(self.auth.enrollment(token))
        self.fill('手机当前动态码', otp.at(self.now[0]))
        self.press('完成绑定')
        self.assertEqual(self.auth.principal(token), 'demo_user_a')
        return otp

    def login(self):
        self.fill('用户名', self.username); self.fill('登录密码', self.password)
        self.press('登录')

    def count_transfers(self):
        with self.store.connection() as connection:
            return connection.execute('SELECT COUNT(*) FROM transfers').fetchone()[0]

    def test_setup_interruption_resumes_same_codes_then_binds(self):
        token, codes = self.begin()
        self.assertEqual(len(codes), 8)
        self.assertEqual(self.count_transfers(), 0)
        self.page = self.fresh(); self.login()
        replacement = self.page.session_state['auth_token']
        self.assertEqual(self.auth.pending_recovery(replacement), codes)
        self.acknowledge_and_bind()
        self.assertEqual(self.auth.pending_recovery(replacement), [])
        with self.store.connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM auth_users').fetchone()[0], 1)

    def test_real_transaction_wrong_code_relogin_and_one_payment(self):
        token, codes = self.begin(); otp = self.acknowledge_and_bind()
        self.now[0] += 30
        self.page.chat_input[0].set_value('给李明转1200元').run()
        flow = self.page.session_state['conversation']
        self.assertEqual(flow.result['status'], 'awaiting_verification')
        valid = {otp.at(self.now[0] + shift) for shift in (-30, 0, 30)}
        wrong = next(str(number).zfill(6) for number in range(10) if str(number).zfill(6) not in valid)
        self.fill('本次交易的手机动态码', wrong); self.press('验证并继续')
        self.assertEqual(self.count_transfers(), 0)
        self.fill('本次交易的手机动态码', otp.at(self.now[0])); self.press('验证并继续')
        self.page.run()
        self.assertEqual(self.count_transfers(), 1)
        self.now[0] += 3601; self.page.run(); self.login(); self.page.run()
        self.assertEqual(self.count_transfers(), 1)
        self.assertEqual(self.page.session_state['conversation'].result['status'], 'succeeded')
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0], 80000)

    def test_phone_recovery_in_page_revokes_old_session_without_payment(self):
        token, codes = self.begin(); self.acknowledge_and_bind()
        self.page.button(key='auth_logout').click().run()
        self.fill('恢复账号', self.username); self.fill('账号密码', self.password)
        self.fill('一枚未使用的恢复码', codes[0]); self.press('撤销原会话并重新绑定')
        with self.assertRaises(Exception): self.auth.principal(token)
        self.assertEqual(self.count_transfers(), 0)
        self.login(); new_token = self.page.session_state['auth_token']
        otp = pyotp.parse_uri(self.auth.enrollment(new_token))
        self.fill('手机当前动态码', otp.at(self.now[0])); self.press('完成绑定')
        self.assertEqual(self.auth.remaining_recovery(new_token), 7)
        self.assertEqual(self.count_transfers(), 0)


class ProcessAndGuardTests(unittest.TestCase):
    def test_live_launcher_server_and_second_open(self):
        import launcher
        with socket.socket() as socket_handle:
            socket_handle.bind(('127.0.0.1', 0)); port = socket_handle.getsockname()[1]
        with TemporaryDirectory() as td:
            stop_file = Path(td) / 'stop'
            code = '''import sys,time
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
from launcher import Launcher
stop=Path(sys.argv[3])
def wait(child):
    deadline=time.monotonic()+50
    while time.monotonic()<deadline and not stop.exists():
        if child.poll() is not None:return child.returncode
        time.sleep(.1)
    child.terminate();child.wait(timeout=10);return 0
raise SystemExit(Launcher(root=root,port=int(sys.argv[2]),open_browser=lambda:None,wait_child=wait).run())
'''
            process = subprocess.Popen([sys.executable, '-B', '-c', code, str(ROOT), str(port), str(stop_file)],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            try:
                deadline=time.monotonic()+25; ready=False
                while time.monotonic()<deadline and process.poll() is None:
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/_stcore/health',timeout=.5) as response:
                            ready=response.status==200
                    except Exception:pass
                    if ready:break
                    time.sleep(.15)
                self.assertTrue(ready)
                record_path=ROOT/'runtime/app_launcher.json'
                record=json.loads(record_path.read_text(encoding='utf-8'))
                second=launcher.Launcher(root=ROOT, port=port, open_browser=lambda:None)
                self.assertEqual(second.run(),0)
                self.assertEqual(json.loads(record_path.read_text(encoding='utf-8'))['app_pid'],record['app_pid'])
                stop_file.touch();self.assertEqual(process.wait(timeout=15),0)
                self.assertFalse(record_path.exists())
            finally:
                stop_file.touch()
                if process.poll() is None:process.wait(timeout=20)

    def test_guard_denies_unknown_listener_and_does_not_touch_unittest(self):
        import bank.first_run as guard
        self.assertFalse(guard.setup_allowed(None))
        self.assertFalse(guard.setup_allowed({'Host': '127.0.0.1:8501'}, server_address='0.0.0.0'))
        self.assertTrue(guard.setup_allowed({'Host': '127.0.0.1:8501'}, server_address='127.0.0.1'))
        self.assertNotIn('_installed_stage_test_selector', vars(guard))
        import launcher
        with TemporaryDirectory() as td:
            with launcher.startup_lock(td) as first:
                self.assertTrue(first)
                with launcher.startup_lock(td) as second:
                    self.assertFalse(second)
            with launcher.startup_lock(td) as third:
                self.assertTrue(third)

    def test_real_worker_stops_then_restarts_with_saved_preference(self):
        self.assertTrue(pyotp.__file__)
        with TemporaryDirectory() as td:
            store = Store(Path(td) / 'bank.sqlite3'); initialize(store)
            controller = BackgroundController(store, root=ROOT)
            processes = []
            def wait_for(predicate, seconds=12):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    if predicate(): return
                    time.sleep(0.15)
                self.fail('Real worker did not reach the required state')
            try:
                first = controller.ensure_started(); processes.append(controller._last_process)
                self.assertTrue(first['started']); wait_for(lambda: controller.status()['running'])
                pid = controller.status()['pid']; first_tick = controller.status()['last_tick']
                self.assertFalse(controller.ensure_started()['started'])
                self.assertEqual(controller.status()['pid'], pid)
                wait_for(lambda: controller.status()['last_tick'] != first_tick)
                exited = normal_exit(store, 'demo_user_a', timeout=6)
                self.assertTrue(exited['app_exit_allowed']); self.assertTrue(controller.preference())
                processes[-1].wait(timeout=8)
                self.assertTrue(controller.ensure_started(enable=False)['started'])
                processes.append(controller._last_process)
                wait_for(lambda: controller.status()['running'])
                self.assertNotEqual(controller.status()['pid'], pid)
                stopped = controller.request_stop(wait=6)
                self.assertTrue(stopped['confirmed_stopped']); self.assertFalse(controller.preference())
                processes[-1].wait(timeout=8)
                with store.connection() as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM transfers').fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0], 200000)
            finally:
                controller.request_stop(wait=2)
                for process in processes:
                    if process is not None:
                        if process.poll() is None: process.terminate()
                        process.wait(timeout=8)


if __name__ == '__main__':
    unittest.main(verbosity=2)
