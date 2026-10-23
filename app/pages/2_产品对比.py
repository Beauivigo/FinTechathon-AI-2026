from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.demo import gateway, PRINCIPAL
from shared import ServiceError, ContractError, adapt_request, validate_result

st.set_page_config(page_title='模拟产品对比',layout='wide')
from app import ui

from app.auth_ui import gate
gate()
ui.render_sidebar_nav('products')
ui.render_page_header('产品对比')
ui.render_app_nav('products')

with st.container(border=True):
    st.info('以下为 D 第一轮定版的 6 个虚构产品（WP-C01—C06），字段与共享接口一致。仅做对比展示，不推荐产品；评估、申购、持仓与赎回请到“理财”页。')
    try:
        gw = gateway()
        catalog = gw.get_context(principal_id=PRINCIPAL)['products']
    except (ServiceError, ContractError, OSError):
        st.error('模拟产品暂不可用。'); st.stop()
    names = {p['product_id']:p['name'] for p in catalog}
    selected = st.multiselect('选择产品',list(names),default=list(names)[:2],format_func=lambda x:names[x],key='product_ids')
    if st.button('对比所选产品',disabled=not selected,key='product_compare'):
        try:
            req = adapt_request('wealth.compare',{'product_ids':selected},request_id='r_'+uuid4().hex,trace_id='t_'+uuid4().hex)
            result = validate_result(req,gw.execute(req,principal_id=PRINCIPAL))
            if result['status'] != 'succeeded': raise ServiceError('UPSTREAM_ERROR','查询失败')
            st.dataframe([{'产品':p['name'],'风险':p['risk_level'],
                '起购金额（分）':p['minimum_amount_minor'],'锁定天数':p['lock_days'],
                '费用说明':p['fee_description'],'赎回条件':p['redemption_rule']} for p in result['data']['products']],hide_index=True)
        except (ServiceError,ContractError):
            st.error('本次对比未完成，请重新核对产品。')
