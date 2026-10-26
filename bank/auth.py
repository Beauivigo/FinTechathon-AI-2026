"""Local password sessions and phone TOTP.

First-run account creation is an in-app, local-only flow; the CLI
``python -m bank.auth`` bootstrap remains for administrators and isolated
tests.  Passwords, QR payloads, TOTP secrets/codes and recovery codes never
enter chat, model settings, URLs or logs.  Only salted password hashes,
SHA-256 recovery-code hashes and a DPAPI-sealed pending recovery package are
stored in SQLite.  The package is deleted as soon as the user confirms they
saved the recovery codes.
"""
import hashlib
import hmac
import json
import secrets
import time

from shared import ServiceError

AUTH_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS auth_users(principal_id TEXT PRIMARY KEY REFERENCES principals, username TEXT UNIQUE NOT NULL,
       salt BLOB NOT NULL, password_hash BLOB NOT NULL, secret BLOB, enabled INTEGER NOT NULL DEFAULT 0,
       last_step INTEGER NOT NULL DEFAULT -1, failures INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
       login_failures INTEGER NOT NULL DEFAULT 0, login_locked_until REAL NOT NULL DEFAULT 0,
       setup_state TEXT NOT NULL DEFAULT 'active', recovery_ack INTEGER NOT NULL DEFAULT 1, setup_package BLOB)''',
    '''CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,principal_id TEXT NOT NULL REFERENCES auth_users,
       expires_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS auth_recovery(code_hash TEXT PRIMARY KEY,principal_id TEXT NOT NULL REFERENCES auth_users)''',
    '''CREATE TABLE IF NOT EXISTS auth_proofs(challenge_id TEXT PRIMARY KEY REFERENCES challenges,session_hash TEXT NOT NULL)''',
)

SETUP_RECOVERY = 'recovery_pending'
SETUP_BINDING = 'binding_pending'
SETUP_ACTIVE = 'active'
RECOVERY_CODE_COUNT = 8
SESSION_SECONDS = 3600


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _valid_password(password):
    return isinstance(password, str) and 12 <= len(password) <= 256


class AuthService:
    def __init__(self, store, *, clock=time.time, seal=None, unseal=None):
        self.store = store
        self.clock = clock
        if seal is None or unseal is None:
            from agent.model_credentials import _dpapi
            self.seal = lambda raw: _dpapi(raw, decrypt=False)
            self.unseal = lambda raw: _dpapi(raw, decrypt=True)
        else:
            self.seal, self.unseal = seal, unseal

    @staticmethod
    def password_hash(password, salt):
        if not _valid_password(password):
            raise ServiceError('AUTH_INVALID', '密码须为12—256字符。')
        return hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3,
                              maxmem=128 * 1024 * 1024, dklen=32)

    def _new_session(self, c, principal):
        token = secrets.token_urlsafe(40)
        c.execute('INSERT INTO auth_sessions VALUES (?,?,?)',
                  (digest(token), principal, self.clock() + SESSION_SECONDS))
        return token

    def _row(self, c):
        return c.execute('SELECT * FROM auth_users LIMIT 1').fetchone()

    @staticmethod
    def _state(row):
        return (row['setup_state'] if row is not None and row['setup_state'] else SETUP_ACTIVE)

    def has_account(self):
        with self.store.connection() as c:
            return bool(self._row(c))

    def account_status(self):
        """Safe status for the welcome/login screen; never returns hashes or codes."""
        with self.store.connection() as c:
            row = self._row(c)
            if row is None:
                return dict(initialized=False, username=None, bound=False,
                            setup_state=None, recovery_remaining=0)
            remaining = c.execute('SELECT COUNT(*) FROM auth_recovery WHERE principal_id=?',
                                  (row['principal_id'],)).fetchone()[0]
        return dict(initialized=True, username=row['username'], bound=bool(row['enabled']),
                    setup_state=self._state(row), recovery_remaining=remaining)

    def session_status(self, token):
        if not isinstance(token, str) or not token:
            return 'invalid'
        with self.store.connection() as c:
            row = c.execute('SELECT expires_at FROM auth_sessions WHERE token_hash=?',
                            (digest(token),)).fetchone()
            if row is None:
                return 'invalid'
            return 'active' if row['expires_at'] > self.clock() else 'expired'

    def login_detail(self, username, password):
        """Return (token, error, retry_after_seconds) without exposing account internals."""
        token = None
        error = 'INVALID'
        retry = 0
        lookup = username.strip() if isinstance(username, str) else username
        with self.store.connection(write=True) as c:
            row = c.execute('SELECT * FROM auth_users WHERE username=?', (lookup,)).fetchone()
            now = self.clock()
            if row is not None:
                if row['login_locked_until'] > now:
                    error, retry = 'LOCKED', max(1, int(row['login_locked_until'] - now + 1))
                else:
                    try:
                        valid = hmac.compare_digest(self.password_hash(password, row['salt']), row['password_hash'])
                    except ServiceError:
                        valid = False
                    if valid:
                        token = self._new_session(c, row['principal_id'])
                        c.execute('UPDATE auth_users SET login_failures=0,login_locked_until=0 WHERE principal_id=?',
                                  (row['principal_id'],))
                        error = None
                    else:
                        failures = row['login_failures'] + 1
                        locked = failures >= 5
                        c.execute('''UPDATE auth_users SET login_failures=?,login_locked_until=?
                            WHERE principal_id=?''',
                                  (failures, now + 600 if locked else row['login_locked_until'],
                                   row['principal_id']))
                        if locked:
                            error, retry = 'LOCKED', 600
        return token, error, retry

    def login(self, username, password):
        token, error, _ = self.login_detail(username, password)
        if token is None:
            raise ServiceError('AUTH_INVALID', '登录未通过或暂时锁定。')
        return token

    def identity(self, c, token, *, require_bound=False):
        row = c.execute('SELECT u.* FROM auth_sessions s JOIN auth_users u USING(principal_id) WHERE token_hash=? AND expires_at>?',
                        (digest(token or ''), self.clock())).fetchone()
        if row is None or (require_bound and not row['enabled']):
            raise ServiceError('AUTH_INVALID', '请登录并完成手机验证器绑定。')
        return row

    def principal(self, token, *, require_bound=True):
        with self.store.connection() as c:
            return self.identity(c, token, require_bound=require_bound)['principal_id']

    def logout(self, token):
        with self.store.connection(write=True) as c:
            c.execute('UPDATE challenges SET proof_digest=NULL WHERE challenge_id IN (SELECT challenge_id FROM auth_proofs WHERE session_hash=?)',
                      (digest(token or ''),))
            c.execute('DELETE FROM auth_proofs WHERE session_hash=?', (digest(token or ''),))
            c.execute('DELETE FROM auth_sessions WHERE token_hash=?', (digest(token or ''),))

    def bootstrap(self, username, password, *, principal='demo_user_a'):
        """Trusted local CLI/test initialisation; never public registration."""
        if not isinstance(username, str) or not username.strip() or len(username) > 64:
            raise ServiceError('INVALID_REQUEST', '用户名无效。')
        salt = secrets.token_bytes(16)
        hashed = self.password_hash(password, salt)
        codes = [secrets.token_urlsafe(20) for _ in range(RECOVERY_CODE_COUNT)]
        with self.store.connection(write=True) as c:
            if self._row(c) is not None:
                raise ServiceError('INVALID_STATE', '身份已初始化，禁止覆盖。')
            c.execute('''INSERT INTO auth_users
                (principal_id,username,salt,password_hash,setup_state,recovery_ack)
                VALUES (?,?,?,?,?,1)''', (principal, username.strip(), salt, hashed, SETUP_ACTIVE))
            c.executemany('INSERT INTO auth_recovery VALUES (?,?)',
                          [(digest(code), principal) for code in codes])
        return codes

    def begin_setup(self, username, password, confirm):
        """Create the single local account and a sealed, resumable recovery package."""
        if not isinstance(username, str) or not username.strip() or len(username.strip()) > 64:
            raise ServiceError('INVALID_REQUEST', '用户名无效。')
        name = username.strip()
        if not _valid_password(password):
            raise ServiceError('INVALID_REQUEST', '密码须为12—256字符。')
        if not isinstance(confirm, str) or not hmac.compare_digest(password.encode('utf-8'), confirm.encode('utf-8')):
            raise ServiceError('INVALID_REQUEST', '两次输入的密码不一致。')
        salt = secrets.token_bytes(16)
        hashed = self.password_hash(password, salt)
        codes = [secrets.token_urlsafe(20) for _ in range(RECOVERY_CODE_COUNT)]
        try:
            package = self.seal(json.dumps(codes).encode('utf-8'))
        except Exception as exc:
            raise ServiceError('INVALID_STATE', '本机加密存储暂不可用，未创建账号；请检查本机配置。') from exc
        token = None
        with self.store.connection(write=True) as c:
            if self._row(c) is not None:
                raise ServiceError('INVALID_STATE', '身份已初始化，禁止覆盖；请使用现有账号登录或恢复。')
            c.execute('''INSERT INTO auth_users
                (principal_id,username,salt,password_hash,setup_state,recovery_ack,setup_package)
                VALUES (?,?,?,?,?,0,?)''',
                      ('demo_user_a', name, salt, hashed, SETUP_RECOVERY, package))
            c.executemany('INSERT INTO auth_recovery VALUES (?,?)',
                          [(digest(code), 'demo_user_a') for code in codes])
            token = self._new_session(c, 'demo_user_a')
        return dict(token=token, codes=codes, recovery_remaining=len(codes))

    def pending_recovery(self, token):
        """Return the sealed codes only while the user has not confirmed saving them."""
        with self.store.connection() as c:
            row = self.identity(c, token)
            if self._state(row) != SETUP_RECOVERY or row['recovery_ack']:
                return []
            package = row['setup_package']
        if not package:
            raise ServiceError('INVALID_STATE', '恢复码暂不可读取，请使用密码和已保存的恢复码处理。')
        try:
            value = json.loads(self.unseal(bytes(package)).decode('utf-8'))
        except Exception as exc:
            raise ServiceError('INVALID_STATE', '恢复码暂不可读取，未放宽任何身份校验。') from exc
        if not isinstance(value, list) or not value or not all(isinstance(code, str) for code in value):
            raise ServiceError('INVALID_STATE', '恢复码记录不一致，未放宽任何身份校验。')
        return value

    def confirm_recovery_saved(self, token):
        with self.store.connection(write=True) as c:
            row = self.identity(c, token)
            state = self._state(row)
            if state == SETUP_RECOVERY:
                c.execute('''UPDATE auth_users SET recovery_ack=1,setup_package=NULL,setup_state=?
                    WHERE principal_id=?''', (SETUP_BINDING, row['principal_id']))
            elif not (state == SETUP_BINDING and row['recovery_ack']):
                raise ServiceError('INVALID_STATE', '当前没有待确认的恢复码。')

    def remaining_recovery(self, token):
        with self.store.connection() as c:
            row = self.identity(c, token)
            return c.execute('SELECT COUNT(*) FROM auth_recovery WHERE principal_id=?',
                             (row['principal_id'],)).fetchone()[0]

    def enrollment(self, token):
        import pyotp
        with self.store.connection(write=True) as c:
            row = self.identity(c, token)
            if row['enabled']:
                raise ServiceError('INVALID_STATE', '验证器已绑定；换绑须先安全恢复。')
            if not row['recovery_ack'] or self._state(row) == SETUP_RECOVERY:
                raise ServiceError('INVALID_STATE', '请先确认已离线保存一次性恢复码。')
            secret = self.unseal(row['secret']).decode() if row['secret'] else pyotp.random_base32()
            if not row['secret']:
                c.execute('UPDATE auth_users SET secret=? WHERE principal_id=?',
                          (self.seal(secret.encode()), row['principal_id']))
            return pyotp.TOTP(secret).provisioning_uri(row['username'], issuer_name='FinTechathon Local')

    def failure(self, c, principal):
        c.execute('''UPDATE auth_users SET failures=failures+1,
            locked_until=CASE WHEN failures+1>=5 THEN ? ELSE locked_until END
            WHERE principal_id=?''', (self.clock() + 600, principal))

    def consume(self, c, token, code, *, enroll=False):
        self.store.require_write_transaction(c)
        import pyotp
        row = self.identity(c, token)
        if row['locked_until'] > self.clock() or not row['secret'] or (not enroll and not row['enabled']):
            return False
        secret = self.unseal(row['secret']).decode()
        totp = pyotp.TOTP(secret)
        now = int(self.clock() // 30)
        for step in (now, now - 1, now + 1):
            if (step > row['last_step'] and isinstance(code, str) and len(code) == 6
                    and code.isascii() and code.isdigit()
                    and hmac.compare_digest(totp.at(step * 30), code)):
                c.execute('UPDATE auth_users SET last_step=?,enabled=1,failures=0 WHERE principal_id=?',
                          (step, row['principal_id']))
                return True
        self.failure(c, row['principal_id'])
        return False

    def finish_enrollment(self, token, code):
        with self.store.connection(write=True) as c:
            row = self.identity(c, token)
            valid = self.consume(c, token, code, enroll=True)
            if valid:
                c.execute('''UPDATE auth_users SET setup_state=?,recovery_ack=1,setup_package=NULL
                    WHERE principal_id=?''', (SETUP_ACTIVE, row['principal_id']))
        if not valid:
            raise ServiceError('AUTH_INVALID', '动态码未通过、已使用或暂时锁定。')

    def recover(self, username, password, code):
        valid = False
        if not isinstance(code, str) or not code:
            raise ServiceError('AUTH_INVALID', '恢复未通过或暂时锁定。')
        lookup = username.strip() if isinstance(username, str) else username
        with self.store.connection(write=True) as c:
            row = c.execute('SELECT * FROM auth_users WHERE username=?', (lookup,)).fetchone()
            if row and row['locked_until'] <= self.clock():
                try:
                    password_ok = hmac.compare_digest(self.password_hash(password, row['salt']), row['password_hash'])
                except ServiceError:
                    password_ok = False
                recovery = c.execute('SELECT 1 FROM auth_recovery WHERE code_hash=? AND principal_id=?',
                                     (digest(code), row['principal_id'])).fetchone()
                if password_ok and recovery:
                    c.execute('DELETE FROM auth_recovery WHERE code_hash=?', (digest(code),))
                    c.execute('DELETE FROM auth_sessions WHERE principal_id=?', (row['principal_id'],))
                    c.execute('UPDATE challenges SET proof_digest=NULL WHERE principal_id=?', (row['principal_id'],))
                    c.execute('DELETE FROM auth_proofs WHERE challenge_id IN (SELECT challenge_id FROM challenges WHERE principal_id=?)',
                              (row['principal_id'],))
                    c.execute('''UPDATE auth_users SET secret=NULL,enabled=0,last_step=-1,failures=0,
                        setup_state=?,recovery_ack=1,setup_package=NULL WHERE principal_id=?''',
                              (SETUP_ACTIVE, row['principal_id']))
                    valid = True
                else:
                    self.failure(c, row['principal_id'])
        if not valid:
            raise ServiceError('AUTH_INVALID', '恢复未通过或暂时锁定。')


def main():
    import getpass
    from .store import Store
    from .seed import initialize, demo_path
    store = Store(demo_path())
    initialize(store)
    codes = AuthService(store).bootstrap(input('本机用户名：'), getpass.getpass('新密码（至少12字符）：'))
    print('一次性恢复码，请离线保管，不要上传或发送给AI：')
    print('\n'.join(codes))


if __name__ == '__main__':
    main()
