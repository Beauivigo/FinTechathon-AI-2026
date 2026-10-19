"""预算预留与可信剩余释放：与商户提交共用串行写事务。"""
from uuid import uuid4

from shared import ServiceError, operation_digest
from shared.adapters import loads_request, loads_result, strict_json_loads
from shared.services import validate_task_spec
from .services import owned_account

ACTIONS = frozenset({'budget.reserve', 'budget.release'})


def checked_reservation(c, reservation_id, principal):
    row = c.execute('SELECT * FROM reservations WHERE reservation_id=? AND principal_id=?',
                    (reservation_id, principal)).fetchone()
    if row is None:
        raise ServiceError('FORBIDDEN', '预留不存在或不属于当前用户。')
    owned_account(c, row['account_id'], principal)
    return row


def binding(c, request, principal):
    p = request['params']
    if request['action'] == 'budget.reserve':
        owned_account(c, p['account_id'], principal)
        return [p['account_id']]
    row = checked_reservation(c, p['reservation_id'], principal)
    if row['state'] in ('released', 'closed'):
        raise ServiceError('INVALID_STATE', '预留已经结清，请查询原操作。')
    # 冻结状态须先核实；部分消费只能释放未被在途订单占用的剩余金额。
    if row['state'] not in ('active','partially_consumed'):
        raise ServiceError('INVALID_STATE', '预留需核实，不能直接释放。')
    saved = c.execute("SELECT request_json,result_json,digest FROM operations WHERE principal_id=? AND action='budget.reserve' AND idempotency_key=?",
                      (principal, row['reserve_key'])).fetchone()
    if saved is None:
        raise ServiceError('INVALID_STATE', '缺少原预留提交证明。')
    original = loads_request(saved['request_json'])
    result = loads_result(original, saved['result_json'])
    if (original['action'] != 'budget.reserve' or original['idempotency_key'] != row['reserve_key']
            or operation_digest(original, principal_id=principal) != saved['digest']
            or original['params'] != dict(account_id=row['account_id'], amount_minor=row['amount_minor'], purpose=row['purpose'])
            or result['status'] != 'succeeded' or result['source'] != 'sandbox'
            or result['data'] != dict(reservation_id=row['reservation_id'], amount_minor=row['amount_minor'])):
        raise ServiceError('INVALID_STATE', '原预留结果尚未核实。')
    from .merchant import unresolved
    held = unresolved(c,row['reservation_id'],principal)
    consumed = c.execute('SELECT COALESCE(sum(amount_minor),0) FROM merchant_orders WHERE reservation_id=?',(row['reservation_id'],)).fetchone()[0]
    if consumed + row['remaining_reserved_minor'] > row['amount_minor']:
        raise ServiceError('INVALID_STATE','预留与消费对账不一致。')
    for task in c.execute('SELECT spec_json,state FROM tasks WHERE principal_id=?', (principal,)):
        spec = validate_task_spec(strict_json_loads(task[0]))
        if spec['payload'].get('params', {}).get('reservation_id') == row['reservation_id'] and task['state']!='cancelled':
            raise ServiceError('INVALID_STATE', '预留存在未核实的关联任务。')
    releasable = row['remaining_reserved_minor'] - held
    if releasable <= 0:
        raise ServiceError('INVALID_STATE','没有已核实可释放的剩余金额。')
    return dict(row,release_amount_minor=releasable,consumed_minor=consumed)


def apply(store, c, request, principal):
    """调用者必须已完成授权；原操作回执和审计由网关在同一保存点写入。"""
    store.require_write_transaction(c)
    bound = binding(c, request, principal)
    p = request['params']
    if request['action'] == 'budget.reserve':
        account = owned_account(c, p['account_id'], principal)
        amount = p['amount_minor']
        if account['available_minor'] < amount:
            raise ServiceError('INSUFFICIENT_FUNDS', '可用余额不足。')
        rid = 'reservation_' + uuid4().hex
        c.execute('INSERT INTO reservations VALUES (?,?,?,?,?,?,?,?)',
                  (rid, principal, p['account_id'], request['idempotency_key'], amount, amount, p['purpose'], 'active'))
        c.execute('UPDATE accounts SET available_minor=available_minor-?,reserved_minor=reserved_minor+? WHERE account_id=?',
                  (amount, amount, p['account_id']))
        data = dict(reservation_id=rid, amount_minor=amount)
        effect = 'reserve'
    else:
        rid = bound['reservation_id']
        amount = bound['release_amount_minor']
        c.execute('UPDATE accounts SET available_minor=available_minor+?,reserved_minor=reserved_minor-? WHERE account_id=?',
                  (amount, amount, bound['account_id']))
        remaining = bound['remaining_reserved_minor'] - amount
        state = ('closed' if bound['consumed_minor'] else 'released') if remaining==0 else ('partially_consumed' if bound['consumed_minor'] else 'active')
        c.execute('UPDATE reservations SET remaining_reserved_minor=?,state=? WHERE reservation_id=?',(remaining,state,rid))
        data = dict(reservation_id=rid)
        effect = 'release_reserved'
    # 无收支流水。独立的子动作键与金额只进入审计，不扩充公开release回执。
    return data, dict(reservation_id=rid, effect=effect, amount_minor=amount, posting_key=rid+'_'+effect+'_'+request['idempotency_key'])
