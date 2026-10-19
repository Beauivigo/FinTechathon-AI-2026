"""D 第一轮定版的 6 个虚构产品。

净值、费率是内部参数，不进入公开的 products 输出；公开输出仍只有契约规定的
七个字段（product_id、name、risk_level、minimum_amount_minor、lock_days、
fee_description、redemption_rule）。
"""

from copy import deepcopy

from shared.contracts import RECORDS, fields

PRODUCTS = (
    dict(product_id="WP-C01", name="现金钱包（虚构）", risk_level="R1",
         minimum_amount_minor=100, lock_days=0, net_value_minor=100,
         purchase_fee_bps=0, redeem_fee_bps=0,
         fee_description="申购费0、赎回费0（虚构现金管理类，不承诺收益）",
         redemption_rule="随时可申请赎回，T+1到账"),
    dict(product_id="WP-C02", name="稳健短债（虚构）", risk_level="R2",
         minimum_amount_minor=10000, lock_days=7, net_value_minor=100,
         purchase_fee_bps=10, redeem_fee_bps=0,
         fee_description="申购费0.1%（四舍五入到分），赎回费0（虚构）",
         redemption_rule="持有满7天后可申请赎回，T+1到账"),
    dict(product_id="WP-C03", name="平衡混合（虚构）", risk_level="R3",
         minimum_amount_minor=50000, lock_days=30, net_value_minor=125,
         purchase_fee_bps=50, redeem_fee_bps=25,
         fee_description="申购费0.5%、赎回费0.25%（均四舍五入到分，虚构）",
         redemption_rule="持有满30天后可申请赎回，T+1到账"),
    dict(product_id="WP-C04", name="成长股票（虚构）", risk_level="R4",
         minimum_amount_minor=100000, lock_days=90, net_value_minor=200,
         purchase_fee_bps=100, redeem_fee_bps=50,
         fee_description="申购费1.0%、赎回费0.5%（均四舍五入到分，虚构）",
         redemption_rule="持有满90天后可申请赎回，T+1到账"),
    dict(product_id="WP-C05", name="进取先锋（虚构）", risk_level="R5",
         minimum_amount_minor=200000, lock_days=180, net_value_minor=50,
         purchase_fee_bps=150, redeem_fee_bps=75,
         fee_description="申购费1.5%、赎回费0.75%（均四舍五入到分，虚构）",
         redemption_rule="持有满180天后可申请赎回，T+1到账"),
    dict(product_id="WP-C06", name="目标日期2035（虚构）", risk_level="R3",
         minimum_amount_minor=100000, lock_days=60, net_value_minor=100,
         purchase_fee_bps=80, redeem_fee_bps=40,
         fee_description="申购费0.8%、赎回费0.4%（均四舍五入到分，虚构）",
         redemption_rule="持有满60天后可申请赎回，T+1到账"),
)

PUBLIC_FIELDS = tuple(RECORDS["product"][0])


def products():
    """公开产品目录；只返回契约七字段，不含净值与费率。"""
    rows = []
    for item in PRODUCTS:
        row = {name: item[name] for name in PUBLIC_FIELDS}
        fields(row, *RECORDS["product"])
        rows.append(row)
    return deepcopy(rows)


def catalog():
    return {item["product_id"]: deepcopy(item) for item in PRODUCTS}


def product(product_id):
    for item in PRODUCTS:
        if item["product_id"] == product_id:
            return deepcopy(item)
    return None


def public_product(product_id):
    """单个产品的公开视图；不存在时返回 None。"""
    item = product(product_id)
    if item is None:
        return None
    return {name: item[name] for name in PUBLIC_FIELDS}
