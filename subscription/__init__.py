"""E固定模拟候选；不执行自动识别、代扣或通知。"""
from shared import ContractError, ServiceError
from shared.contracts import RECORDS, fields
from shared.services import validate_subscription_change


def record(row):
    result = {k:row[k] for k in RECORDS['subscription'][0]}
    fields(result, *RECORDS['subscription'])
    return result


def checked_review(c, request, principal):
    row = c.execute('''SELECT subscriptions.* FROM subscriptions JOIN accounts USING(account_id)
        WHERE subscription_id=? AND principal_id=?''',(request['params']['subscription_id'],principal)).fetchone()
    if row is None:
        raise ServiceError('FORBIDDEN','无权操作此订阅。')
    target = 'active' if request['params']['decision']=='accept' else 'dismissed'
    try:
        validate_subscription_change(request,row['subscription_state'],target)
        record(row)
    except ContractError as exc:
        raise ServiceError('INVALID_STATE','只能核实或排除候选订阅。') from exc
    return row, target
