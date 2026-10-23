"""可信页面的商户任务服务；复用 task spec，不新增模型工具或隐式付款。"""
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
import json

from shared import ServiceError, adapt_request, operation_digest, validate_result
from shared.contracts import check_value
from shared.services import validate_task_spec
from . import merchant, budget
from .scheduler import Scheduler, owned_task, task_from, transition


class MerchantScheduler(Scheduler):
    def list_tasks(self, *, principal_id):
        return [t for t in super().list_tasks(principal_id=principal_id)
                if t['spec']['payload'].get('action') == 'merchant.order']

    def register(self, params, run_at, registration_key, *, principal_id, confirmed=False):
        """只供可信页面显式确认调用；重试同键同内容，不修改预留或创建授权。"""
        if confirmed is not True:
            raise ServiceError('INVALID_REQUEST', '请单独确认登记的固定采购内容。')
        check_value(run_at, 'datetime', 'run_at')
        check_value(registration_key, 'id', 'registration_key')
        digest = sha256(json.dumps([principal_id, registration_key]).encode()).hexdigest()
        request = adapt_request('merchant.order', deepcopy(params), request_id='merchant_req_'+digest,
            trace_id='merchant_trace_'+digest, idempotency_key='merchant_exec_'+digest)
        if not self.gateway.demo_mode:request['schema_version']='1.2'
        with self.store.connection(write=True) as c:
            reservation = budget.checked_reservation(c, params['reservation_id'], principal_id)
            spec = validate_task_spec(dict(schema_version='1.1', task_key='merchant_task_'+digest,
                trace_id=request['trace_id'], run_at=run_at, kind='execute_request', origin_action='budget.reserve',
                origin_idempotency_key=reservation['reserve_key'], payload=request))
            existing = c.execute('SELECT * FROM tasks WHERE principal_id=? AND task_key=?',
                                 (principal_id, spec['task_key'])).fetchone()
            if existing:
                task = task_from(existing)
                if task['spec'] != spec:
                    raise ServiceError('IDEMPOTENCY_CONFLICT', '登记编号对应的采购内容已变化。')
                return task
            now = self.clock()
            when = datetime.fromisoformat(run_at.replace('Z', '+00:00'))
            if when <= now:
                raise ServiceError('INVALID_REQUEST', '登记时间须晚于当前时间；不设未经确认的提前上限。')
            if date.fromisoformat(params['deliver_on']) <= when.astimezone(merchant.SHANGHAI).date():
                raise ServiceError('INVALID_REQUEST', '按既有商户规则，配送日期须晚于计划执行日期。')
            # 核对原预留成功证明，以及当前商品、地址、卡和可用预留；不消耗预算。
            budget.binding(c, adapt_request('budget.release', dict(reservation_id=params['reservation_id']),
                request_id='check_'+digest, trace_id=request['trace_id'], idempotency_key='check_'+digest), principal_id, check_tasks=False)
            merchant.binding(c, request, principal_id, now)
            if self.gateway._load(c, request, principal_id):
                raise ServiceError('INVALID_STATE', '执行键已存在，不能重新登记。')
            task_id = 'merchant_schedule_'+digest
            # 兼容旧表必填外键：商户任务保存付款账户，绝不解释成转账收款人。
            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,NULL)',
                (task_id, principal_id, spec['task_key'], json.dumps(spec), 'scheduled', params['account_id']))
            self.gateway._audit(c, request, principal_id, 'merchant_task_registered', now, task_id=task_id)
            return task_from(owned_task(c, task_id, principal_id))

    def _owned(self, c, task_id, principal):
        row = owned_task(c, task_id, principal)
        if task_from(row)['spec']['payload']['action'] != 'merchant.order':
            raise ServiceError('INVALID_REQUEST', '不是商户任务。')
        return row

    def run_once(self, *, principal_id):
        """只受理到期操作并产生新授权，不授权付款；原键查询优先。"""
        results = []
        for task in self.list_tasks(principal_id=principal_id):
            if task['state'] not in ('scheduled', 'running', 'unknown'):
                continue
            with self.store.connection(write=True) as c:
                row = self._owned(c, task['task_id'], principal_id)
                if row['state'] == 'scheduled':
                    if self.clock() < datetime.fromisoformat(task['spec']['run_at'].replace('Z', '+00:00')):
                        continue
                    transition(c, row, 'running', self.clock())
                elif row['state'] not in ('running', 'unknown'):
                    continue
            request = task['spec']['payload']
            try:
                query = adapt_request('operation.get', dict(original_action='merchant.order',
                    original_idempotency_key=request['idempotency_key']), request_id=request['request_id']+'_query',
                    trace_id=request['trace_id'])
                reply = validate_result(query, self.gateway.execute(query, principal_id=principal_id))
                if reply['status'] == 'succeeded':
                    original = reply['data']['operation_request']
                    if operation_digest(original, principal_id=principal_id) != operation_digest(request, principal_id=principal_id):
                        raise ServiceError('RESULT_UNKNOWN', '执行键内容不一致。')
                    result = reply['data']['operation_result']
                elif reply['status'] == 'failed' and reply['error']['code'] == 'NOT_FOUND':
                    # 同键再投递只会创建待授权操作；未知时不会更换执行键。
                    result = validate_result(request, self.gateway.execute(request, principal_id=principal_id))
                else:
                    raise ServiceError('RESULT_UNKNOWN', '原执行结果待核实。')
                state = 'unknown' if result['status'] in ('running', 'unknown') else 'dispatched'
                results.append(self._mark(task['task_id'], principal_id, state))
            except Exception:
                results.append(self._mark(task['task_id'], principal_id, 'unknown', 'result_unknown'))
        return results

    def cancel(self, task_id, *, principal_id, confirmed=False):
        """取消未领取任务或原待授权订单；与最终扣款共用串行事务，不回滚已提交。"""
        if confirmed is not True:
            raise ServiceError('INVALID_REQUEST', '请确认取消任务。')
        with self.store.connection(write=True) as c:
            row = self._owned(c, task_id, principal_id)
            task = task_from(row)
            if row['state'] == 'cancelled':
                return task
            if row['state'] == 'scheduled':
                return transition(c, row, 'cancelled', self.clock())
            request = task['spec']['payload']
            saved = self.gateway._load(c, request, principal_id)
            if saved is None or saved[1]['status'] in ('running', 'unknown', 'succeeded'):
                raise ServiceError('INVALID_STATE', '已提交或结果待核实，不能取消或推断未付款。')
            result = saved[1]
            if result['status'] in ('awaiting_confirmation', 'awaiting_verification'):
                challenge, original, result = self.gateway._challenge(c, result['next_action']['challenge_id'], principal_id)
                self.gateway._finish(c, challenge, original, result, principal_id, self.clock(), 'cancelled')
            # dispatched 表示派发历史；订单取消不改写 task spec 或既有任务状态契约。
            if row['state'] in ('running', 'unknown'):
                return transition(c, row, 'dispatched', self.clock())
            return task
