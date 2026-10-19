"""理财公开查询：产品目录与对比。

产品数据来自 D 第一轮定版的虚构目录（`wealth.catalog`）；评分、申购、赎回由
`bank.wealth` 接入网关，不在本模块执行资金操作。
"""

from shared import ContractError, ServiceError
from shared.contracts import ACTIONS, fields

from .catalog import public_product, products

__all__ = ["products", "compare"]


def compare(params):
    try:
        fields(params, ACTIONS["wealth.compare"]["required"])
    except ContractError as exc:
        raise ServiceError("INVALID_REQUEST", "产品选择无效。") from exc
    rows = []
    for product_id in params["product_ids"]:
        row = public_product(product_id)
        if row is None:
            raise ServiceError("NOT_FOUND", "产品编号不存在。")
        rows.append(row)
    return {"products": rows}
