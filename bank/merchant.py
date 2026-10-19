"""E模拟商品与商户消费。只在网关的已授权串行写事务内提交。"""
from datetime import date, datetime, timedelta, timezone
import json
from uuid import uuid4

from shared import ServiceError, operation_digest
from shared.adapters import loads_request, loads_result
from shared.services import validate_funds_command, validate_funds_receipt
from .services import owned_account
from .store import utc_timestamp

ITEMS = (
    dict(item_id='ITEM-BD-FLOWER-01', name='模拟生日鲜花束', price_minor=19900),
    dict(item_id='ITEM-BD-FLOWER-02', name='模拟玫瑰礼盒', price_minor=29900),
    dict(item_id='ITEM-BD-CAKE-01', name='模拟生日蛋糕（6寸）', price_minor=15800),
    dict(item_id='ITEM-BD-CAKE-02', name='模拟生日蛋糕（8寸）', price_minor=22800),
)
ADDRESSES = (dict(delivery_address_id='ADDR-BD-01', label='模拟家里'),
             dict(delivery_address_id='ADDR-BD-02', label='模拟公司'))
SHANGHAI = timezone(timedelta(hours=8))
TERMINAL_UNUSED = {'failed', 'cancelled', 'blocked'}


def operation(c, principal, key):
    row = c.execute("SELECT * FROM operations WHERE principal_id=? AND action='merchant.order' AND idempotency_key=?",(principal,key)).fetchone()
    if row is None:
        raise ServiceError('INVALID_STATE','缺少可信的订单操作记录。')
    request = loads_request(row['request_json'])
    result = loads_result(request,row['result_json'])
    if (request['action']!='merchant.order' or request['idempotency_key']!=key
            or operation_digest(request,principal_id=principal)!=row['digest']
            or result['source'] not in ('gateway','sandbox')):
        raise ServiceError('INVALID_STATE','订单证据不一致。')
    return request,result


def unresolved(c, rid, principal, excluding=None):
    """只有可信受理记录能按金额隔离；旧/不完整关联一律阻断。"""
    total=0
    for row in c.execute("SELECT * FROM operations WHERE principal_id=? AND action='merchant.order'",(principal,)):
        request=loads_request(row['request_json'])
        if request['params']['reservation_id']!=rid:
            continue
        key=request['idempotency_key']
        request,result=operation(c,principal,key)
        intent=c.execute('SELECT * FROM merchant_intents WHERE principal_id=? AND operation_key=?',(principal,key)).fetchone()
        order=c.execute('SELECT * FROM merchant_orders WHERE principal_id=? AND operation_key=?',(principal,key)).fetchone()
        if result['status'] in TERMINAL_UNUSED and order is None:
            continue  # 网关终态不可重新授权；不是将NOT_FOUND解释为未执行。
        if intent is None or intent['reservation_id']!=rid or intent['amount_minor']!=request['params']['amount_minor']:
            raise ServiceError('INVALID_STATE','订单归属金额无法核实，不能释放或继续消费。')
        if result['status']=='succeeded':
            if (order is None or order['reservation_id']!=rid or order['amount_minor']!=intent['amount_minor']
                    or result['data']!=dict(order_id=order['order_id'],amount_minor=order['amount_minor'])):
                raise ServiceError('INVALID_STATE','已提交订单证据不完整。')
        elif order is not None:
            raise ServiceError('INVALID_STATE','订单与操作状态不一致，须核实。')
        elif key!=excluding:
            total+=intent['amount_minor']
    return total


def binding(c, request, principal, now):
    p=request['params']; owned_account(c,p['account_id'],principal)
    from .budget import checked_reservation
    reservation=checked_reservation(c,p['reservation_id'],principal)
    if reservation['account_id']!=p['account_id']:
        raise ServiceError('FORBIDDEN','预留不属于付款账户。')
    if reservation['state'] not in ('active','partially_consumed'):
        raise ServiceError('INVALID_STATE','预留已结清或待核实。')
    item=next((i for i in ITEMS if i['item_id']==p['item_id']),None)
    if item is None or p['delivery_address_id'] not in {a['delivery_address_id'] for a in ADDRESSES}:
        raise ServiceError('NOT_FOUND','模拟商品或地址不存在。')
    if item['price_minor']!=p['amount_minor'] or date.fromisoformat(p['deliver_on'])<=now.astimezone(SHANGHAI).date():
        raise ServiceError('INVALID_REQUEST','价格须与目录一致，配送日期须晚于今天。')
    for task in c.execute('SELECT spec_json,state FROM tasks WHERE principal_id=?',(principal,)):
        spec=json.loads(task['spec_json'])
        if spec['payload'].get('idempotency_key')==request['idempotency_key']:
            if task['state'] not in ('running','dispatched','unknown') or now<datetime.fromisoformat(spec['run_at'].replace('Z','+00:00')):
                raise ServiceError('INVALID_STATE','关联商户任务未到期、未派发或已取消。')
            if operation_digest(spec['payload'],principal_id=principal)!=operation_digest(request,principal_id=principal):
                raise ServiceError('IDEMPOTENCY_CONFLICT','商户任务内容不能改变。')
    held=unresolved(c,p['reservation_id'],principal,request['idempotency_key'])
    if reservation['remaining_reserved_minor']-held<p['amount_minor']:
        raise ServiceError('INSUFFICIENT_FUNDS','该预留的未占用金额不足。')
    cards=c.execute('SELECT * FROM cards WHERE account_id=? ORDER BY card_id',(p['account_id'],)).fetchall()
    if 'card_id' in p:
        card=next((r for r in cards if r['card_id']==p['card_id']),None)
        if card is None: raise ServiceError('FORBIDDEN','指定卡不属于付款账户。')
        cards=[card]
    if not cards: raise ServiceError('NOT_FOUND','账户没有可用卡片。')
    active=[r for r in cards if r['card_state']=='active']
    if not active: raise ServiceError('INVALID_STATE','卡片须为正常状态。')
    eligible=[r for r in active if r['limit_minor']>=p['amount_minor']]
    if not eligible: raise ServiceError('LIMIT_EXCEEDED','单笔金额超过卡片限额。')
    card=eligible[0]
    return [card['card_id'],card['account_id'],card['card_state'],card['limit_minor'],card['revision']]


def apply(store,c,request,principal,now):
    store.require_write_transaction(c)
    card=binding(c,request,principal,now); p=request['params']; amount=p['amount_minor']
    rid=p['reservation_id']; oid='order_'+uuid4().hex; entry='entry_'+uuid4().hex; key=oid+'_consume'
    command=dict(schema_version='1.1',posting_key=key,trace_id=request['trace_id'],account_id=p['account_id'],reference_id=oid,effect='consume_reserved',amount_minor=amount,kind='consumption',reservation_id=rid)
    validate_funds_command(command)
    c.execute('UPDATE accounts SET balance_minor=balance_minor-?,reserved_minor=reserved_minor-? WHERE account_id=?',(amount,amount,p['account_id']))
    c.execute("UPDATE reservations SET remaining_reserved_minor=remaining_reserved_minor-?,state=CASE WHEN remaining_reserved_minor=? THEN 'closed' ELSE 'partially_consumed' END WHERE reservation_id=?",(amount,amount,rid))
    item=next(i for i in ITEMS if i['item_id']==p['item_id'])
    c.execute('''INSERT INTO ledger_entries(transaction_id,account_id,reference_id,posting_key,amount_minor,direction,kind,category,occurred_at,merchant_name)
        VALUES (?,?,?,?,?,'debit','consumption',?,?,?)''',(entry,p['account_id'],oid,key,amount,'鲜花' if 'FLOWER' in p['item_id'] else '蛋糕',utc_timestamp(now.isoformat()),item['name']))
    receipt={k:command[k] for k in ('posting_key','account_id','reference_id','effect','amount_minor')}; receipt['entry_id']=entry
    validate_funds_receipt(command,receipt)
    c.execute('INSERT INTO fund_postings VALUES (?,?,?,?,?)',(key,principal,entry,json.dumps(command),json.dumps(receipt)))
    c.execute('INSERT INTO merchant_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)',(oid,principal,request['idempotency_key'],rid,p['account_id'],card[0],p['item_id'],amount,p['deliver_on'],p['delivery_address_id'],entry))
    return dict(order_id=oid,amount_minor=amount),dict(reservation_id=rid,entry_id=entry,posting_key=key,effect='consume_reserved',amount_minor=amount,card_id=card[0])
