from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.demo import gateway, PRINCIPAL
from app.money import yuan_text
from shared import ServiceError, ContractError, adapt_request, validate_result

st.set_page_config(page_title='产品对比',layout='wide')
from app import ui
from app import visual

# 先注入固定静态样式，再渲染页面内容；st.set_page_config 已在本文件最前面调用。
visual.apply('core')
from app.auth_ui import gate
gate()
ui.render_sidebar_nav('products')
with visual.band('core', 'products'):
    ui.render_page_header('产品对比')
    ui.render_app_nav('products')

with visual.card('core', 'main'):
    st.caption('只做产品对比，不构成推荐；评估、申购与赎回请到“理财”。')
    with st.expander('目录说明'):
        st.caption('6 个虚构产品（WP-C01—C06），字段与共享接口一致。')
    try:
        gw = gateway()
        catalog = gw.get_context(principal_id=PRINCIPAL)['products']
    except (ServiceError, ContractError, OSError):
        st.error('产品数据暂不可用。'); st.stop()
    names = {p['product_id']:p['name'] for p in catalog}
    selected = st.multiselect('选择产品',list(names),default=list(names)[:2],format_func=lambda x:names[x],key='product_ids')
    if st.button('对比所选产品',disabled=not selected,key='product_compare'):
        try:
            req = adapt_request('wealth.compare',{'product_ids':selected},request_id='r_'+uuid4().hex,trace_id='t_'+uuid4().hex)
            result = validate_result(req,gw.execute(req,principal_id=PRINCIPAL))
            if result['status'] != 'succeeded': raise ServiceError('UPSTREAM_ERROR','查询失败')
            from app.presentation import render_authorization
            render_authorization(gw, req, principal_id=PRINCIPAL)
            ui.render_status(result['status'])
            ui.render_records([{'产品':p['name'],'风险':p['risk_level'],
                '起购金额（元）':yuan_text(p['minimum_amount_minor']),'锁定天数':p['lock_days'],
                '费用说明':p['fee_description'],'赎回条件':p['redemption_rule']} for p in result['data']['products']],hide_index=True,
                title_field='产品', value_field='起购金额（元）',
                meta_fields=('风险', '锁定天数', '费用说明', '赎回条件'),
                key='product_comparison_records', layout='tiles', tone='core', limit=None,
                table_view=True, details_label='参数对比')
            st.caption('对比结果不构成推荐；申购、费用、份额与赎回仍按理财页现有规则。')
        except (ServiceError,ContractError):
            st.error('本次对比未完成，请重新核对产品。')
    chosen = st.selectbox('选择要继续办理的产品', selected, format_func=names.get, index=None,placeholder='请选择', key='product_purchase_choice')
    if st.button('去理财办理所选产品',key='product_to_wealth',disabled=chosen is None):
        st.session_state['wealth_product_id'] = chosen
        st.switch_page('pages/9_理财.py')
