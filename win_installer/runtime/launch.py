"""Own the server process and its shutdown; never kill an arbitrary recorded PID."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser
from contextlib import contextmanager
from paths import INSTALL, SOURCE, safe_data, configure


@contextmanager
def lock():
    import msvcrt
    data = safe_data()
    data.mkdir(parents=True, exist_ok=True)
    path = data / 'launcher.lock'
    if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
        raise RuntimeError('启动锁路径无效。')
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        acquired = False
        try:
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                pass
            yield acquired
        finally:
            if acquired:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def healthy(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=.5) as r:
            return r.status == 200 and r.read() == b'ok'
    except OSError:
        return False


def record():
    path = safe_data() / 'launcher.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('install') == str(INSTALL) and 1024 <= value['port'] <= 65535:
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8511)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        raise ValueError('端口必须在1024到65535之间。')
    with lock() as owned:
        if not owned:
            old = record()
            if old and healthy(old['port']):
                if not args.no_browser:
                    webbrowser.open(f"http://127.0.0.1:{old['port']}")
                return 0
            raise RuntimeError('言行正在启动或退出，请稍后重试。')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', args.port))
        data = configure()
        run = uuid.uuid4().hex
        rec = data / 'launcher.json'
        stop = data / 'stop-request.txt'
        rec.write_text(json.dumps(dict(install=str(INSTALL), port=args.port, nonce=run)), encoding='utf-8')
        stop.unlink(missing_ok=True)
        env = os.environ.copy()
        for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
            env.pop(name, None)
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
        command = [str(INSTALL / 'python' / 'python.exe'), '-B', '-X', 'utf8', '-m',
                   'streamlit', 'run', str(SOURCE / 'app' / 'main.py'),
                   '--server.address', '127.0.0.1', '--server.port', str(args.port),
                   '--server.headless', 'true', '--browser.gatherUsageStats', 'false']
        child = None
        try:
            with (data / 'application.log').open('ab', buffering=0) as log:
                child = subprocess.Popen(command, cwd=SOURCE, env=env, stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
                deadline = time.monotonic() + 60
                while not healthy(args.port):
                    if child.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('页面未启动，请查看本机数据目录中的 application.log。')
                    time.sleep(.3)
                if not args.no_browser:
                    webbrowser.open(f'http://127.0.0.1:{args.port}')
                while child.poll() is None:
                    if stop.exists() and stop.read_text(encoding='utf-8') == run:
                        from bank.background import BackgroundController
                        from bank.store import Store
                        db = data / 'runtime' / 'sandbox.sqlite3'
                        if db.exists():
                            result = BackgroundController(Store(db), root=data).request_stop(wait=8, preserve_preference=True)
                            if result.get('live'):
                                raise RuntimeError('后台尚未停止，请在应用中退出后重试。')
                        child.terminate()
                        child.wait(timeout=15)
                        break
                    time.sleep(.3)
                return child.returncode or 0
        finally:
            if child is not None and child.poll() is None:
                child.terminate(); child.wait(timeout=15)
            rec.unlink(missing_ok=True)
            stop.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        if '--no-browser' in sys.argv:
            print(str(exc), file=sys.stderr)
        else:
            ctypes.windll.user32.MessageBoxW(None, str(exc), '言行启动未完成', 0x10)
        raise SystemExit(1)
