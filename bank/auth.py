"""Local password sessions and phone TOTP. Bootstrap is a local CLI, never public registration."""
import hashlib
import hmac
import secrets
import time
from shared import ServiceError

AUTH_SCHEMA=(
    '''CREATE TABLE IF NOT EXISTS auth_users(principal_id TEXT PRIMARY KEY REFERENCES principals, username TEXT UNIQUE NOT NULL,
       salt BLOB NOT NULL, password_hash BLOB NOT NULL, secret BLOB, enabled INTEGER NOT NULL DEFAULT 0,
       last_step INTEGER NOT NULL DEFAULT -1, failures INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
       login_failures INTEGER NOT NULL DEFAULT 0, login_locked_until REAL NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,principal_id TEXT NOT NULL REFERENCES auth_users,
       expires_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS auth_recovery(code_hash TEXT PRIMARY KEY,principal_id TEXT NOT NULL REFERENCES auth_users)''',
    '''CREATE TABLE IF NOT EXISTS auth_proofs(challenge_id TEXT PRIMARY KEY REFERENCES challenges,session_hash TEXT NOT NULL)''',
)


def digest(value): return hashlib.sha256(value.encode()).hexdigest()


class AuthService:
    def __init__(self,store,*,clock=time.time,seal=None,unseal=None):
        self.store=store;self.clock=clock
        if seal is None or unseal is None:
            from agent.model_credentials import _dpapi
            self.seal=lambda raw:_dpapi(raw,decrypt=False)
            self.unseal=lambda raw:_dpapi(raw,decrypt=True)
        else:self.seal=seal;self.unseal=unseal

    @staticmethod
    def password_hash(password,salt):
        if not isinstance(password,str) or not 12<=len(password)<=256:
            raise ServiceError('AUTH_INVALID','密码须为12—256字符。')
        return hashlib.scrypt(password.encode(),salt=salt,n=32768,r=8,p=3,maxmem=128*1024*1024,dklen=32)

    def bootstrap(self,username,password,*,principal='demo_user_a'):
        if not isinstance(username,str) or not username.strip() or len(username)>64:raise ValueError('用户名无效。')
        salt=secrets.token_bytes(16);hashed=self.password_hash(password,salt)
        codes=[secrets.token_urlsafe(20) for _ in range(8)]
        with self.store.connection(write=True) as c:
            if c.execute('SELECT 1 FROM auth_users LIMIT 1').fetchone():
                raise ServiceError('INVALID_STATE','身份已初始化，禁止覆盖。')
            c.execute('INSERT INTO auth_users(principal_id,username,salt,password_hash) VALUES (?,?,?,?)',(principal,username.strip(),salt,hashed))
            c.executemany('INSERT INTO auth_recovery VALUES (?,?)',[(digest(code),principal) for code in codes])
        return codes

    def login(self,username,password):
        token=None
        with self.store.connection(write=True) as c:
            row=c.execute('SELECT * FROM auth_users WHERE username=?',(username,)).fetchone()
            if row and row['login_locked_until']<=self.clock():
                try:valid=hmac.compare_digest(self.password_hash(password,row['salt']),row['password_hash'])
                except ServiceError:valid=False
                if valid:
                    token=secrets.token_urlsafe(40)
                    c.execute('INSERT INTO auth_sessions VALUES (?,?,?)',(digest(token),row['principal_id'],self.clock()+3600))
                    c.execute('UPDATE auth_users SET login_failures=0 WHERE principal_id=?',(row['principal_id'],))
                else:
                    c.execute('UPDATE auth_users SET login_failures=login_failures+1,login_locked_until=CASE WHEN login_failures+1>=5 THEN ? ELSE login_locked_until END WHERE principal_id=?',(self.clock()+600,row['principal_id']))
        if token is None:raise ServiceError('AUTH_INVALID','登录未通过或暂时锁定。')
        return token

    def identity(self,c,token,*,require_bound=False):
        row=c.execute('SELECT u.* FROM auth_sessions s JOIN auth_users u USING(principal_id) WHERE token_hash=? AND expires_at>?',
                      (digest(token or ''),self.clock())).fetchone()
        if row is None or (require_bound and not row['enabled']):raise ServiceError('AUTH_INVALID','请登录并完成手机验证器绑定。')
        return row

    def principal(self,token,*,require_bound=True):
        with self.store.connection() as c:return self.identity(c,token,require_bound=require_bound)['principal_id']

    def logout(self,token):
        with self.store.connection(write=True) as c:
            c.execute('UPDATE challenges SET proof_digest=NULL WHERE challenge_id IN (SELECT challenge_id FROM auth_proofs WHERE session_hash=?)',(digest(token or ''),))
            c.execute('DELETE FROM auth_proofs WHERE session_hash=?',(digest(token or ''),))
            c.execute('DELETE FROM auth_sessions WHERE token_hash=?',(digest(token or ''),))

    def enrollment(self,token):
        import pyotp
        with self.store.connection(write=True) as c:
            row=self.identity(c,token)
            if row['enabled']:raise ServiceError('INVALID_STATE','验证器已绑定；换绑须先安全恢复。')
            secret=self.unseal(row['secret']).decode() if row['secret'] else pyotp.random_base32()
            if not row['secret']:c.execute('UPDATE auth_users SET secret=? WHERE principal_id=?',(self.seal(secret.encode()),row['principal_id']))
            return pyotp.TOTP(secret).provisioning_uri(row['username'],issuer_name='FinTechathon Local')

    def failure(self,c,principal):
        c.execute('UPDATE auth_users SET failures=failures+1,locked_until=CASE WHEN failures+1>=5 THEN ? ELSE locked_until END WHERE principal_id=?',
                  (self.clock()+600,principal))

    def consume(self,c,token,code,*,enroll=False):
        self.store.require_write_transaction(c)
        import pyotp
        row=self.identity(c,token)
        if row['locked_until']>self.clock() or not row['secret'] or (not enroll and not row['enabled']):return False
        secret=self.unseal(row['secret']).decode();totp=pyotp.TOTP(secret)
        now=int(self.clock()//30)
        for step in (now,now-1,now+1):
            if step>row['last_step'] and isinstance(code,str) and len(code)==6 and code.isascii() and code.isdigit() and hmac.compare_digest(totp.at(step*30),code):
                c.execute('UPDATE auth_users SET last_step=?,enabled=1,failures=0 WHERE principal_id=?',(step,row['principal_id']))
                return True
        self.failure(c,row['principal_id']);return False

    def finish_enrollment(self,token,code):
        with self.store.connection(write=True) as c:valid=self.consume(c,token,code,enroll=True)
        if not valid:raise ServiceError('AUTH_INVALID','动态码未通过、已使用或暂时锁定。')

    def recover(self,username,password,code):
        valid=False
        with self.store.connection(write=True) as c:
            row=c.execute('SELECT * FROM auth_users WHERE username=?',(username,)).fetchone()
            if row and row['locked_until']<=self.clock():
                try:password_ok=hmac.compare_digest(self.password_hash(password,row['salt']),row['password_hash'])
                except ServiceError:password_ok=False
                recovery=c.execute('SELECT 1 FROM auth_recovery WHERE code_hash=? AND principal_id=?',(digest(code),row['principal_id'])).fetchone()
                if password_ok and recovery:
                    c.execute('DELETE FROM auth_recovery WHERE code_hash=?',(digest(code),))
                    c.execute('DELETE FROM auth_sessions WHERE principal_id=?',(row['principal_id'],))
                    c.execute('UPDATE challenges SET proof_digest=NULL WHERE principal_id=?',(row['principal_id'],))
                    c.execute('DELETE FROM auth_proofs WHERE challenge_id IN (SELECT challenge_id FROM challenges WHERE principal_id=?)',(row['principal_id'],))
                    c.execute('UPDATE auth_users SET secret=NULL,enabled=0,last_step=-1,failures=0 WHERE principal_id=?',(row['principal_id'],))
                    valid=True
                else:self.failure(c,row['principal_id'])
        if not valid:raise ServiceError('AUTH_INVALID','恢复未通过或暂时锁定。')


def main():
    import getpass
    from .store import Store
    from .seed import initialize,demo_path
    store=Store(demo_path());initialize(store)
    codes=AuthService(store).bootstrap(input('本机用户名：'),getpass.getpass('新密码（至少12字符）：'))
    print('一次性恢复码，请离线保管，不要上传或发送给AI：')
    print('\n'.join(codes))


if __name__=='__main__':main()
