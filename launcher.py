"""Double-click friendly launcher for the already-configured Windows machine.

This is not an installer.  It uses the project's existing ``.venv`` and the
already-installed Streamlit dependency, refuses to create a second app server,
and never resets the database.  Closing the browser page does not stop the
application or its background checks; the in-app "退出应用" control (or
closing this launcher console) is the normal exit path.
"""
from contextlib import contextmanager
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_PORT = 8501


def _http_healthy(port, timeout=0.8):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{int(port)}/_stcore/health', timeout=timeout) as response:
            return response.status == 200 and response.read().strip() == b'ok'
    except Exception:
        return False


def _port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(('127.0.0.1', int(port))) == 0


def _pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def streamlit_command(python, port, root=ROOT):
    return [str(python), '-B', '-X', 'utf8', '-m', 'streamlit', 'run',
            str(Path(root) / 'app' / 'main.py'),
            '--server.address', '127.0.0.1',
            '--server.port', str(int(port)),
            '--server.headless', 'true',
            '--browser.gatherUsageStats', 'false']


def _stop_background():
    """Request only this application's recorded worker to stop; never kill a PID."""
    try:
        from bank.background import BackgroundController
        from bank.seed import demo_path
        from bank.store import Store
        path = demo_path()
        if not path.exists():
            return dict(skipped=True)
        return BackgroundController(Store(path)).request_stop(wait=5, preserve_preference=True)
    except Exception as exc:
        return dict(error=type(exc).__name__)


@contextmanager
def startup_lock(root):
    """OS releases this per-project lock even if a launcher crashes."""
    runtime = Path(root) / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    handle = (runtime / 'launcher.lock').open('a+b')
    locked = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            pass
        yield locked
    finally:
        if locked:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


class Launcher:
    def __init__(self, *, root=ROOT, port=APP_PORT, python=None, probe=None, port_busy=None,
                 pid_alive=None, spawn=None, open_browser=None, stop_background=None,
                 sleep=time.sleep, check_environment=None, wait_child=None,
                 startup_timeout=45.0):
        self.root = Path(root)
        self.port = int(port)
        self.python = python or sys.executable
        self.probe = probe or (lambda: _http_healthy(self.port))
        self.port_busy = port_busy or (lambda: _port_busy(self.port))
        self.pid_alive = pid_alive or _pid_alive
        self.spawn = spawn or self._spawn
        self.open_browser = open_browser or (lambda: webbrowser.open(f'http://127.0.0.1:{self.port}'))
        self.stop_background = stop_background or _stop_background
        self.sleep = sleep
        self.check_environment = check_environment or self._check_environment
        self.wait_child = wait_child or (lambda child: child.wait())
        self.startup_timeout = startup_timeout
        self.record_path = self.root / 'runtime' / 'app_launcher.json'

    # ---- environment ---------------------------------------------------------------
    def _check_environment(self):
        if not (self.root / 'app' / 'main.py').is_file():
            return '当前目录缺少 app/main.py，无法启动本应用。'
        try:
            result = subprocess.run([self.python, '-c', 'import streamlit'], cwd=self.root,
                                    capture_output=True, timeout=30)
        except Exception as exc:
            return f'无法检查页面依赖（{type(exc).__name__}）；未启动应用。'
        if result.returncode:
            return '当前Python缺少页面依赖 streamlit。请使用已配置的 .venv；全新电脑安装不在本轮范围。'
        return None

    # ---- launcher record ------------------------------------------------------------
    def read_record(self):
        try:
            value = json.loads(self.record_path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    def write_record(self, value):
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.record_path.with_name(self.record_path.name + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, self.record_path)

    def remove_record(self):
        try:
            self.record_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    # ---- process --------------------------------------------------------------------
    def _spawn(self):
        return subprocess.Popen(streamlit_command(self.python, self.port, self.root), cwd=self.root)

    def _wait_healthy(self, child):
        deadline = time.monotonic() + max(1.0, self.startup_timeout)
        while time.monotonic() < deadline:
            if child.poll() is not None:
                return False
            if self.probe():
                return True
            self.sleep(0.5)
        return False

    @staticmethod
    def _terminate(child):
        try:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except Exception:
                    child.kill()
        except Exception:
            pass

    def run(self):
        with startup_lock(self.root) as owned:
            if not owned:
                if self.probe():
                    self.open_browser()
                    return 0
                print('本应用正在启动，请稍后再次打开；没有启动第二套服务。')
                return 2
            return self._run_owned()

    def _run_owned(self):
        problem = self.check_environment()
        if problem:
            print(problem)
            return 4
        record = self.read_record()
        if record and self.pid_alive(record.get('app_pid')) and self.probe():
            print(f'本应用已在运行：http://127.0.0.1:{self.port}（没有启动第二个服务，数据未修改）')
            self.open_browser()
            return 0
        if self.port_busy():
            if self.probe():
                print(f'端口 {self.port} 已被一个网页服务占用。若本应用已在浏览器打开，请直接访问 '
                      f'http://127.0.0.1:{self.port}；否则请关闭占用该端口的程序后重试。数据未修改。')
            else:
                print(f'端口 {self.port} 已被其他程序占用，未启动第二个服务；数据库和余额没有变化。')
            return 2
        try:
            child = self.spawn()
        except Exception as exc:
            print(f'启动页面服务失败（{type(exc).__name__}）；数据未修改。')
            return 5
        self.write_record(dict(schema_version=1, port=self.port, app_pid=getattr(child, 'pid', None),
                               launcher_pid=os.getpid(),
                               started_at=datetime.now(timezone.utc).isoformat()))
        if not self._wait_healthy(child):
            self._terminate(child)
            self.remove_record()
            self.stop_background()
            print('页面服务未能在预期时间内就绪，已停止本次启动；数据库和余额没有变化。')
            return 3
        print(f'应用已启动：http://127.0.0.1:{self.port}')
        print('关闭这个窗口会结束本机页面服务；仅关闭浏览器标签页不会停止已启用的后台检查。')
        code = 0
        try:
            self.open_browser()
            code = self.wait_child(child)
        except KeyboardInterrupt:
            self._terminate(child)
            code = 130
        finally:
            self.remove_record()
            stopped = self.stop_background()
            if isinstance(stopped, dict) and stopped.get('live'):
                print('提示：本应用后台尚未确认停止，请重新打开应用后使用“退出应用”检查。')
            print('应用已退出；未完成待办、余额、流水、预留和计划仍保存在本机。')
        return code


def _reexec_with_venv():
    """When double-clicked with a bare Python, run the configured project venv."""
    if os.environ.get('FINTECH_LAUNCHER_REEXEC') == '1' or os.name != 'nt':
        return None
    venv = ROOT / '.venv' / 'Scripts' / 'python.exe'
    try:
        same = Path(sys.executable).resolve() == venv.resolve()
    except OSError:
        same = False
    if venv.is_file() and not same:
        env = os.environ.copy()
        env['FINTECH_LAUNCHER_REEXEC'] = '1'
        try:
            return subprocess.run([str(venv), str(Path(__file__).resolve())] + sys.argv[1:],
                                  cwd=str(ROOT), env=env).returncode
        except OSError:
            return None
    return None


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    forwarded = _reexec_with_venv()
    if forwarded is not None:
        return forwarded
    code = Launcher().run()
    if code:
        try:
            if sys.stdin is not None and sys.stdin.isatty():
                input('启动或运行未完成，按回车关闭窗口。')
        except Exception:
            pass
    return code


if __name__ == '__main__':
    raise SystemExit(main())
