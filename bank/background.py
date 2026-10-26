"""Trusted local background supervisor.

The Streamlit page asks this stdlib-only module to start/stop the existing
``bank.worker`` process.  Coordination is through the SQLite worker lease:

* a start is idempotent and refuses to spawn while a live lease exists, so
  repeated clicks, page refreshes or several open pages cannot create a
  second worker;
* the worker heartbeats the lease and exits on a stop request; no other OS
  process is ever signalled or terminated;
* the enabled/disabled preference is stored in ``app_settings`` and is
  restored when the local application starts again.

A lease expiry or a takeover is not proof that the previous process stopped.
``status``/``request_stop`` therefore distinguish a worker that acknowledged
its stop from one whose outcome cannot be verified.  The worker never
approves a payment.  Stopping it does not cancel existing operations or plans;
their persisted status remains visible for checking.
"""
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

NAME = 'main'
PREFERENCE_KEY = 'background_enabled'
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEASE_SECONDS = 30
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def utc_text(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class BackgroundController:
    def __init__(self, store, *, clock=time.time, sleep=time.sleep, spawn=None, root=None):
        self.store = store
        self.clock = clock
        self.sleep = sleep
        self.root = Path(root) if root is not None else PROJECT_ROOT
        self._spawn = spawn
        self._last_process = None

    # ---- persisted preference -------------------------------------------------
    @staticmethod
    def _setting_row(c):
        return c.execute('SELECT * FROM app_settings WHERE key=?', (PREFERENCE_KEY,)).fetchone()

    @classmethod
    def _enabled(cls, c):
        row = cls._setting_row(c)
        return bool(row and row['value'] == '1')

    def preference(self):
        with self.store.connection() as c:
            return self._enabled(c)

    def set_preference(self, enabled):
        stamp = utc_text(self.clock())
        with self.store.connection(write=True) as c:
            c.execute('''INSERT INTO app_settings(key,value,updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at''',
                      (PREFERENCE_KEY, '1' if enabled else '0', stamp))
            self._event(c, 'preference_enabled' if enabled else 'preference_disabled', stamp)

    # ---- health -----------------------------------------------------------------
    @staticmethod
    def _lease(c):
        return c.execute("SELECT * FROM worker_lease WHERE name=?", (NAME,)).fetchone()

    def _unretired_owners(self):
        """Owners displaced by a takeover that never recorded a clean exit."""
        try:
            with self.store.connection() as c:
                rows = [dict(r) for r in c.execute(
                    "SELECT event,detail FROM background_events WHERE event IN "
                    "('takeover','fenced','worker_stopped') ORDER BY event_id")]
        except Exception:
            return ['unverified']  # Missing audit evidence cannot confirm retirement.
        pending = {}
        for row in rows:
            try:
                detail = json.loads(row['detail'] or '{}')
            except (TypeError, ValueError):
                return ['unverified']
            if not isinstance(detail, dict):
                return ['unverified']
            if row['event'] == 'takeover':
                previous = detail.get('previous')
                if isinstance(previous, str) and previous and not detail.get('clean'):
                    pending[previous] = True
            else:
                owner = detail.get('owner')
                if isinstance(owner, str) and pending.get(owner):
                    pending[owner] = False
        return [owner for owner, flag in pending.items() if flag]

    @staticmethod
    def _stop_state(row, live, stopping, unverified):
        if live:
            if stopping:
                return 'stopping'
            return 'starting' if row['last_tick'] in (None, 'starting') else 'running'
        if row is None:
            return 'unverified' if unverified else 'idle'
        if row['last_tick'] == 'stopped' and row['stop_requested'] and not unverified:
            return 'stopped'
        if row['stop_requested'] or unverified:
            return 'unverified'
        return 'idle'

    def status(self):
        with self.store.connection() as c:
            row = self._lease(c)
            preference = self._enabled(c)
        now = self.clock()
        live = bool(row and row['expires'] > now)
        stopping = live and bool(row['stop_requested'])
        starting = live and not stopping and row['last_tick'] in (None, 'starting')
        running = live and not stopping and not starting
        last_error = row['error'] if row is not None else None
        unverified = self._unretired_owners()
        stop_state = self._stop_state(row, live, stopping, unverified)
        return dict(
            preference=preference, live=live, running=running, stopping=stopping, starting=starting,
            pid=row['pid'] if row is not None else None,
            last_tick=row['last_tick'] if row is not None else None,
            started_at=row['started_at'] if row is not None else None,
            last_error=last_error,
            expires_in=max(0.0, (row['expires'] - now)) if row is not None else 0.0,
            stop_state=stop_state,
            unverified_instances=len(unverified),
            message=self._message(preference, live, stopping, starting, row, stop_state),
        )

    @staticmethod
    def _message(preference, live, stopping, starting, row, stop_state):
        if stopping:
            return '已请求停止：不再领取新任务，正在完成的检查会保留原操作结果。'
        if starting:
            return '后台启动请求已提交，正在等待首次检查。'
        if live:
            return '后台运行中；关闭网页不会停止本应用后台。'
        if stop_state == 'unverified':
            return '无法核实此前实例是否已停止；后台不再领取新任务，已发出的操作请保留编号查询原结果。'
        if stop_state == 'stopped':
            return '后台已确认停止；已登记计划不会取消，重新启用后才会准备到期待办。'
        if not preference:
            return '后台未启用。关闭网页不会自动启动后台检查。'
        if row is not None and row['error']:
            return '后台已启用但未运行：' + str(row['error'])
        return '后台已启用但当前未运行；可重新启用检查。'

    # ---- lifecycle ---------------------------------------------------------------
    def ensure_started(self, *, enable=True):
        """Start at most one worker. Safe to call on every rerun/refresh."""
        if enable:
            self.set_preference(True)
        with self.store.connection() as c:
            preference = self._enabled(c)
        if not preference:
            result = self.status()
            result.update(started=False, reason='disabled')
            return result
        now = self.clock()
        owner = secrets.token_hex(16)
        existing = None
        with self.store.connection(write=True) as c:
            row = self._lease(c)
            if row is not None and row['expires'] > now:
                existing = row
            else:
                stamp = utc_text(now)
                if row is not None and row['owner'] != owner:
                    self._event(c, 'takeover', stamp, json.dumps(
                        {'previous': row['owner'], 'clean': row['last_tick'] in ('stopped', 'exited')}))
                c.execute('''INSERT INTO worker_lease
                    (name,owner,expires,last_tick,error,pid,started_at,stop_requested,stop_requested_at)
                    VALUES (?,?,?,?,NULL,NULL,?,0,NULL)
                    ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires=excluded.expires,
                      last_tick=NULL,error=NULL,pid=NULL,started_at=excluded.started_at,
                      stop_requested=0,stop_requested_at=NULL''',
                          (NAME, owner, now + LEASE_SECONDS, 'starting', stamp))
                self._event(c, 'start_requested', stamp)
        if existing is not None:
            result = self.status()
            result.update(started=False,
                          reason='stopping' if existing['stop_requested'] else 'already_running')
            return result
        try:
            process = self._spawn_worker(owner)
            pid = getattr(process, 'pid', None)
            if pid is None:
                raise OSError('启动器未返回进程编号')
        except Exception:
            with self.store.connection(write=True) as c:
                stamp = utc_text(self.clock())
                c.execute('''UPDATE worker_lease SET expires=0,error=?,last_tick='exited'
                    WHERE name=? AND owner=?''',
                          ('后台启动失败，请检查本机运行环境后重试。', NAME, owner))
                self._event(c, 'start_failed', stamp)
            result = self.status()
            result.update(started=False, reason='failed')
            return result
        with self.store.connection(write=True) as c:
            stamp = utc_text(self.clock())
            c.execute('UPDATE worker_lease SET pid=? WHERE name=? AND owner=?', (pid, NAME, owner))
            self._event(c, 'started', stamp)
        self._last_process = process
        result = self.status()
        result.update(started=True, reason='started')
        return result

    def request_stop(self, *, wait=0, preserve_preference=False):
        """Ask only this application's worker to stop; never kills a process."""
        with self.store.connection() as c:
            row = self._lease(c)
            has_setting = self._setting_row(c) is not None
            preference = self._enabled(c)
        now = self.clock()
        live = bool(row is not None and row['expires'] > now)
        target = row['owner'] if row is not None else None
        if row is None and not has_setting:
            result = self.status()
            result.update(requested=False, confirmed_stopped=True, stop_state='never_started')
            return result
        with self.store.connection(write=True) as c:
            stamp = utc_text(now)
            current = self._lease(c)
            if not preserve_preference:
                c.execute('''INSERT INTO app_settings(key,value,updated_at) VALUES (?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at''',
                          (PREFERENCE_KEY, '0', stamp))
            if current is not None:
                # Keep the intent observable even when the lease already looks
                # expired: expiry alone never proves the old process has died.
                c.execute('''UPDATE worker_lease SET stop_requested=1,
                    stop_requested_at=COALESCE(stop_requested_at,?)
                    WHERE name=? AND owner=?''', (stamp, NAME, current['owner']))
                target = current['owner']
                self._event(c, 'stop_requested', stamp)
        if wait:
            deadline = time.monotonic() + max(0, wait)
            while time.monotonic() < deadline:
                with self.store.connection() as c:
                    current = self._lease(c)
                if current is None or current['owner'] != target or current['last_tick'] == 'stopped':
                    break
                self.sleep(0.2)
        else:
            with self.store.connection() as c:
                current = self._lease(c)
        result = self.status()
        acknowledged = bool(current is not None and target is not None
            and current['owner'] == target and current['stop_requested']
            and current['last_tick'] == 'stopped' and current['expires'] <= self.clock())
        if acknowledged and not self._unretired_owners():
            stop_state = 'stopped'
        elif target is not None and live:
            stop_state = 'stopping'
        else:
            stop_state = 'unverified'
        result.update(requested=target is not None,
                      confirmed_stopped=(stop_state == 'stopped'), stop_state=stop_state)
        return result

    # ---- spawning ------------------------------------------------------------------
    def _spawn_worker(self, owner):
        if self._spawn is not None:
            return self._spawn(owner)
        return _spawn_detached(self.root, owner, self.store.path)

    @staticmethod
    def _event(c, event, stamp, detail=None):
        c.execute('INSERT INTO background_events(occurred_at,event,detail) VALUES (?,?,?)',
                  (stamp, event, detail))


def _spawn_detached(root, owner, database_path):
    """Start the existing worker detached from this Streamlit process."""
    runtime = Path(root) / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    log = open(runtime / 'background.log', 'ab', buffering=0)
    env = os.environ.copy()
    env['FINTECH_WORKER_OWNER'] = owner
    env['FINTECH_BACKGROUND'] = '1'
    command = [sys.executable, '-B', '-X', 'utf8', '-m', 'bank.worker', '--interval', '5', '--database', str(Path(database_path).resolve())]
    options = dict(cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                   stdout=log, stderr=subprocess.STDOUT)
    if os.name == 'nt':
        options['creationflags'] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        options['start_new_session'] = True
    try:
        return subprocess.Popen(command, **options)
    finally:
        log.close()
