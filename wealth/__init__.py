"""只读工程产品目录；样例参数仅用于对比，不是 D 的正式交易规则。"""
from copy import deepcopy
from shared import ContractError, ServiceError
from shared.contracts import ACTIONS, RECORDS, fields

_PRODUCTS = (
    dict(product_id='product_1', name='工程样例一（非正式产品）', risk_level='R2',
         minimum_amount_minor=10000, lock_days=30,
         fee_description='模拟申购费为零（沿用契约样例）', redemption_rule='满30天后可申请赎回（仅样例）'),
    dict(product_id='sample_product_2', name='工程样例二（非正式产品）', risk_level='R1',
         minimum_amount_minor=100, lock_days=0,
         fee_description='待 D 提供，当前不执行费用计算', redemption_rule='待 D 提供，当前不执行赎回'),
    dict(product_id='sample_product_3', name='工程样例三（非正式产品）', risk_level='R4',
         minimum_amount_minor=100000, lock_days=90,
         fee_description='待 D 提供，当前不执行费用计算', redemption_rule='待 D 提供，当前不执行赎回'),
)


def products():
    rows = deepcopy(list(_PRODUCTS))
    for row in rows:
        fields(row, *RECORDS['product'])
    return rows


def compare(params):
    try:
        fields(params, ACTIONS['wealth.compare']['required'])
    except ContractError as exc:
        raise ServiceError('INVALID_REQUEST','产品选择无效。') from exc
    catalog = {p['product_id']: p for p in products()}
    if any(pid not in catalog for pid in params['product_ids']):
        raise ServiceError('NOT_FOUND','产品编号不存在。')
    return {'products':[catalog[pid] for pid in params['product_ids']]}
