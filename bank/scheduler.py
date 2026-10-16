"""一次性模拟转账调度；SQLite持久化、固定请求、无后台守护承诺。"""
from datetime import datetime, timedelta
from hashlib import sha256
import json
from shared import ContractError, ServiceError, adapt_request, operation_digest, validate_result
from shared.services import validate_task, validate_task_spec, validate_task_transition
from .services import require_principal, owned_account
from .store import utc_timestamp


def check_time(execute_at, now):
    when = datetime.fromisoformat(execute_at.replace('Z','+00:00'))
    if not now + timedelta(minutes=1) <= when <= now + timedelta(days=30):
        raise ServiceError('INVALID_REQUEST','定时时间须在服务端当前时间后1分钟至30天内（含边界）。')
    return when


def spec_for(request, principal):
    key = sha256(json.dumps([principal,request['action'],request['idempotency_key']]).encode()).hexdigest()
    p = request['params']
    payload = adapt_request('transfer.create', {k:p[k] for k in ('account_id','payee_id','amount_minor')},
        request_id='scheduled_req_'+key,trace_id=request['trace_id'],idempotency_key='scheduled_exec_'+key)
    return validate_task_spec(dict(schema_version='1.1',task_key='scheduled_task_'+key,trace_id=request['trace_id'],
        run_at=p['execute_at'],kind='execute_request',origin_action='transfer.schedule',
        origin_idempotency_key=request['idempotency_key'],payload=payload))


def task_from(row):
    return validate_task(dict(task_id=row['task_id'],state=row['state'],spec=json.loads(row['spec_json'])))


def owned_task(c, task_id, principal):
    require_principal(c,principal)
    row = c.execute('SELECT * FROM tasks WHERE task_id=? AND principal_id=?',(task_id,principal)).fetchone()
    if row is None:
        raise ServiceError('NOT_FOUND','未找到当前用户的任务。')
    task_from(row)
    return row


def check_execution(c, request, principal, now):
    """固定执行键同时受任务状态约束，取消/过期与派发竞争不能绕开任务。"""
    key = request['idempotency_key']
    if not key.startswith('scheduled_exec_'):
        return
    task_key = 'scheduled_task_'+key.removeprefix('scheduled_exec_')
    row = c.execute('SELECT * FROM tasks WHERE principal_id=? AND task_key=?',(principal,task_key)).fetchone()
    if row is None or row['state'] not in ('running','unknown','dispatched'):
        raise ServiceError('INVALID_STATE','定时任务尚未领取或已结束。')
    spec = task_from(row)['spec']
    if operation_digest(request,principal_id=principal) != operation_digest(spec['payload'],principal_id=principal):
        raise ServiceError('IDEMPOTENCY_CONFLICT','定时执行内容不能改变。')
    existing = c.execute('SELECT 1 FROM operations WHERE principal_id=? AND action=? AND idempotency_key=?',
                        (principal,request['action'],key)).fetchone()
    when = datetime.fromisoformat(spec['run_at'].replace('Z','+00:00'))
    if now < when or (not existing and now > when+timedelta(minutes=5)):
        raise ServiceError('INVALID_STATE','尚未到执行时间或已超过允许迟到范围。')
    p = request['params']
    account = owned_account(c,p['account_id'],principal)
    payee = c.execute('SELECT account_id FROM payees WHERE principal_id=? AND payee_id=?',(principal,p['payee_id'])).fetchone()
    if not payee or payee[0]!=row['payee_account_id']:
        raise ServiceError('AUTH_INVALID','登记时的收款账户已变化。')
    if account['available_minor']<p['amount_minor']:
        raise ServiceError('INSUFFICIENT_FUNDS','到期执行时可用余额不足。')


def transition(c, row, state, now, reason=None):
    before = task_from(row)
    after = validate_task_transition(before,before|dict(state=state))
    if before == after:
        return after
    c.execute('UPDATE tasks SET state=?,failure_reason=? WHERE task_id=?',(state,reason,row['task_id']))
    spec = before['spec']
    c.execute('''INSERT INTO audit_events (principal_id,trace_id,action,outcome,occurred_at,details_json)
        VALUES (?,?,?,?,?,?)''',(row['principal_id'],spec['trace_id'],spec['origin_action'],'task_'+state,
        utc_timestamp(now.isoformat()),json.dumps(dict(task_id=row['task_id'],reason=reason))))
    return after


def register(services, c, spec, principal):
    services.store.require_write_transaction(c)
    validate_task_spec(spec)
    require_principal(c,principal)
    # 本轮只实现一次性转账；商户与提醒仍缺规则，不能借内部入口派发。
    if spec['origin_action'] != 'transfer.schedule' or spec['kind'] != 'execute_request':
        raise ServiceError('INVALID_REQUEST','当前只支持已授权的定时转账。')
    origin = c.execute('SELECT request_json,result_json FROM operations WHERE principal_id=? AND action=? AND idempotency_key=?',
        (principal,spec['origin_action'],spec['origin_idempotency_key'])).fetchone()
    if origin is None:
        raise ServiceError('FORBIDDEN','缺少原登记操作。')
    from shared.adapters import loads_request, loads_result
    request = loads_request(origin['request_json'])
    previous = loads_result(request,origin['result_json'])
    if spec != spec_for(request,principal):
        raise ServiceError('INVALID_REQUEST','任务与原登记内容不一致。')
    existing = c.execute('SELECT * FROM tasks WHERE principal_id=? AND task_key=?',(principal,spec['task_key'])).fetchone()
    if existing:
        task = task_from(existing)
        if task['spec'] != spec:
            raise ServiceError('IDEMPOTENCY_CONFLICT','任务内容不一致。')
        return task
    if previous['status'] not in ('awaiting_confirmation','awaiting_verification'):
        raise ServiceError('INVALID_STATE','原登记操作不能创建任务。')
    check_time(spec['run_at'],services.clock())
    p = spec['payload']['params']
    owned_account(c,p['account_id'],principal)
    payee = c.execute('SELECT account_id FROM payees WHERE principal_id=? AND payee_id=?',(principal,p['payee_id'])).fetchone()
    if payee is None or payee[0] == p['account_id']:
        raise ServiceError('FORBIDDEN','收款对象不可用。')
    task_id = 'schedule_'+spec['task_key'].removeprefix('scheduled_task_')
    c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,NULL)',
              (task_id,principal,spec['task_key'],json.dumps(spec),'scheduled',payee[0]))
    return task_from(owned_task(c,task_id,principal))


class Scheduler:
    def __init__(self, gateway):
        self.gateway, self.store, self.clock = gateway, gateway.store, gateway.clock

    def list_tasks(self, *, principal_id):
        with self.store.connection() as c:
            require_principal(c,principal_id)
            return [task_from(r) for r in c.execute('SELECT * FROM tasks WHERE principal_id=? ORDER BY rowid DESC',(principal_id,))]

    def _mark(self, task_id, principal, state, reason=None):
        with self.store.connection(write=True) as c:
            row = owned_task(c,task_id,principal)
            if row['state'] not in ('running','unknown'):
                return task_from(row)  # 并发恢复者已经落下可靠终态。
            if state=='failed':
                request = task_from(row)['spec']['payload']
                saved = self.gateway._load(c,request,principal)
                if saved:
                    # 查询与预检间另一派发者可能已经提交，不能再声称未派发。
                    state = 'unknown' if saved[1]['status'] in ('running','unknown') else 'dispatched'
                    reason = None
            return transition(c,row,state,self.clock(),reason)

    def run_once(self, *, principal_id):
        """手动检查一次。重启/并发恢复始终先查原执行键，重发也不换键。"""
        results = []
        for task in self.list_tasks(principal_id=principal_id):
            if task['state'] not in ('scheduled','running','unknown'):
                continue
            with self.store.connection(write=True) as c:
                row = owned_task(c,task['task_id'],principal_id)
                task = task_from(row)
                now = self.clock()
                when = datetime.fromisoformat(task['spec']['run_at'].replace('Z','+00:00'))
                if row['state']=='scheduled':
                    if now < when:
                        continue
                    transition(c,row,'running',now)
                    row = owned_task(c,task['task_id'],principal_id)
                    if now > when + timedelta(minutes=5):
                        results.append(transition(c,row,'failed',now,'lateness_expired'))
                        continue
                elif row['state'] not in ('running','unknown'):
                    continue
            request = task['spec']['payload']
            try:
                query = adapt_request('operation.get',dict(original_action='transfer.create',original_idempotency_key=request['idempotency_key']),
                    request_id=request['request_id']+'_query',trace_id=request['trace_id'])
                reply = validate_result(query,self.gateway.execute(query,principal_id=principal_id))
                if reply['status']=='succeeded':
                    original = reply['data']['operation_request']
                    if operation_digest(original,principal_id=principal_id) != operation_digest(request,principal_id=principal_id):
                        raise ServiceError('RESULT_UNKNOWN','执行键对应内容不一致。')
                    result = reply['data']['operation_result']
                elif reply['status']=='failed' and reply['error']['code']=='NOT_FOUND':
                    with self.store.connection() as c:
                        row = owned_task(c,task['task_id'],principal_id)
                        account = owned_account(c,request['params']['account_id'],principal_id)
                        payee = c.execute('SELECT account_id FROM payees WHERE principal_id=? AND payee_id=?',
                            (principal_id,request['params']['payee_id'])).fetchone()
                        reason = ('lateness_expired' if self.clock() > when+timedelta(minutes=5) else
                                  'resource_changed' if not payee or payee[0]!=row['payee_account_id'] else
                                  'insufficient_funds' if account['available_minor']<request['params']['amount_minor'] else None)
                    if reason:
                        results.append(self._mark(task['task_id'],principal_id,'failed',reason));continue
                    result = validate_result(request,self.gateway.execute(request,principal_id=principal_id))
                else:
                    raise ServiceError('RESULT_UNKNOWN','查询执行状态未得到可靠结果。')
                state = 'unknown' if result['status'] in ('unknown','running') else 'dispatched'
                results.append(self._mark(task['task_id'],principal_id,state))
            except ServiceError as exc:
                state = 'failed' if exc.code=='FORBIDDEN' else 'unknown'
                results.append(self._mark(task['task_id'],principal_id,state,exc.code))
            except Exception:
                # 包括执行提交后回执丢失；不能把异常当成未扣款或新建执行键。
                results.append(self._mark(task['task_id'],principal_id,'unknown','result_unknown'))
        return results
