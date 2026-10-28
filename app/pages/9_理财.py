"""模拟理财：风险问卷、产品对比、申购、持仓与赎回。页面不自行计算费用/份额。"""
from pathlib import Path
import sys
from decimal import Decimal, InvalidOperation
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform
from app.money import yuan_text
from app import ui, visual
from shared import ServiceError


def _minor_from_yuan(value):
    from shared.adapters import yuan_to_minor
    try:
        amount = yuan_to_minor(str(value).strip())
    except (TypeError, ValueError):
        return None
    return amount if amount>0 else None


def _units_from_input(value):
    raw=str(value).strip()
    if len(raw)>24 or set(raw)-set('0123456789.'):
        return None
    try:
        units = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not units.is_finite() or units <= 0:
        return None
    exponent = units.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -4:
        return None
    if units>=Decimal('1000000000000000000'):
        return None
    text = format(units, '.4f')
    if Decimal(text) != units:
        return None
    return text


def _units_widget_key(holding_id, units_text):
    return 'wealth_units_' + str(holding_id) + '_' + str(units_text)


st.set_page_config(page_title='理财', layout='wide')
# 先注入固定静态样式，再渲染页面内容；st.set_page_config 已在本文件最前面调用。
visual.apply('core')
from app.auth_ui import gate
gate()
ui.render_sidebar_nav('wealth')
with visual.band('core', 'wealth'):
    ui.render_page_header('理财操作', '评估风险、核对产品，再决定申购或赎回。')
    ui.render_app_nav('wealth')
    ui.render_service_link('products','浏览与对比产品',widget_key='wealth_compare_link')
st.caption('产品与资金均为本机数据，不承诺收益；费用与份额以服务端结果为准。')

flow = current()
try:
    profile = flow.gateway.latest_risk_profile(principal_id=flow.principal_id)
except ServiceError:
    profile = None
    st.warning('已有风险评估暂不可读取，请稍后核实。')
if profile:
    st.session_state['risk_profile_id'] = profile['profile_id']
    st.info('已有风险评估：'+profile['risk_level']+'。可直接选择产品，或重新填写问卷。')
else:
    st.session_state.pop('risk_profile_id', None)
resources = flow.resources
accounts = resources['accounts']
products = resources['products']
holdings = resources.get('holdings', [])
if not accounts:
    st.error('当前用户没有可用账户。')
    st.stop()
account = st.selectbox('账户', accounts, index=0 if len(accounts)==1 else None,
                       placeholder='请选择账户', format_func=lambda a: a['display_name'], key='wealth_account')
if account is None:
    st.caption('请选择本次办理的账户。')
    render(flow, action_prefix='wealth.')
    st.stop()

ui.render_overview([('当前账户', account['display_name']), ('可选产品', str(len(products))), ('持仓笔数', str(len(holdings)))])
risk_panel, trade_panel = st.columns([1, 1.15], gap='large')

with risk_panel:
    with st.expander('风险问卷 wpq_v1', expanded='risk_profile_id' not in st.session_state):
        labels = {'q1': '投资经验', 'q2': '收入稳定性', 'q3': '可投资资产比例', 'q4': '最大回撤接受度', 'q5': '投资目标'}
        # D第一轮§2.1原文；选项ID和计分仍由后端校验。
        options={
            'q1':('不到1年','1—3年','3—5年','5年以上'),
            'q2':('不稳定（自由职业/临时）','一般（兼职/新入职）','稳定（全职）','非常稳定（大型企业长期/体制内）'),
            'q3':('10%以下','10%—30%','30%—50%','50%以上'),
            'q4':('不能接受亏损','5%以内','10%以内','20%及以上'),
            'q5':('保本储蓄','稳健增值','平衡增长','追求高收益'),
        }
        answers = {}
        for question, label in labels.items():
            choice = st.selectbox(label, ['a', 'b', 'c', 'd'], index=None, key='answer_' + question,
                                  placeholder='请选择',
                                  format_func=lambda x,q=question: options[q]['abcd'.index(x)])
            if choice is not None:
                answers[question] = f'{question}_{choice}'
        completed = len(answers) == len(labels)
        st.caption('请逐题选择，未作答不会代入默认答案。')
        if st.button('提交风险评估', key='wealth_assess', disabled=flow.blocked or not completed):
            perform(flow.start_action, 'wealth.assess',
                    dict(questionnaire_version='wpq_v1', answers=answers))
        if not completed:
            st.caption('完成全部 5 题后即可提交评估。')

if flow.request and flow.request['action'] == 'wealth.assess' and flow.result \
        and flow.result['status'] == 'succeeded':
    st.session_state['risk_profile_id'] = flow.result['data']['risk_profile_id']
    st.success(f"风险评估完成：{flow.result['data']['risk_level']}")
    with st.expander('评估回执详情'):
        st.caption('档案编号：' + flow.result['data']['risk_profile_id'])

with trade_panel:
    with ui.business_section('产品申购', description='请先完成风险问卷；申购须核对内容并通过验证。',
                             key=visual.card_key('core', 'wealth-trade')):
        profile_id = st.session_state.get('risk_profile_id')
        if products:
            preferred = st.session_state.pop('wealth_product_id', None)
            if preferred and st.session_state.get('wealth_product', {}).get('product_id') != preferred:
                st.session_state['wealth_product'] = next((p for p in products if p['product_id']==preferred), products[0])
            product_index = next((index for index, item in enumerate(products)
                                  if item['product_id'] == preferred), 0)
            product = st.selectbox('产品', products, index=product_index, key='wealth_product',
                                   format_func=lambda p: f"{p['name']} · {p['risk_level']} · 起购 {yuan_text(p['minimum_amount_minor'])}")
            st.caption('费用：'+product['fee_description'])
            st.caption('锁定期：'+str(product['lock_days'])+'天；赎回条件：'+product['redemption_rule'])
            amount_yuan = st.text_input('申购金额（元）', value='', key='wealth_amount')
            if st.button('核对并申购', key='wealth_purchase', disabled=flow.blocked):
                if not profile_id:
                    st.warning('请先完成风险问卷。')
                elif not account:
                    st.warning('请选择账户。')
                else:
                    amount_minor = _minor_from_yuan(amount_yuan)
                    if amount_minor is None:
                        st.warning('申购金额必须为正数且最多两位小数，不会自动四舍五入。')
                    else:
                        perform(flow.start_action, 'wealth.purchase',
                                dict(account_id=account['account_id'], product_id=product['product_id'],
                                     risk_profile_id=profile_id,
                                     amount_minor=amount_minor))

with ui.business_section('当前持仓', description='查看已有份额，按原规则发起赎回。',
                         key=visual.card_key('core', 'wealth-holdings')):
    if holdings:
        ui.render_records([{'产品': next((p['name'] for p in products if p['product_id']==h['product_id']), h['product_id']), '份额': h['units']} for h in holdings],
                     hide_index=True, title_field='产品', value_field='份额', key='wealth_holding_records',
                     layout='tiles', tone='core', limit=None)
        with st.expander('持仓编号与详情'):
            st.dataframe([{'持仓编号':h['holding_id'],'产品':next((p['name'] for p in products if p['product_id']==h['product_id']),h['product_id']),
                           '份额':h['units']} for h in holdings],hide_index=True)
        chosen = st.selectbox('选择持仓', holdings, key='wealth_holding',
                              format_func=lambda h: next((p['name'] for p in products if p['product_id']==h['product_id']),h['product_id'])+' · '+h['units']+'份')
        units = st.text_input('赎回份额', value=chosen['units'],
                                key=_units_widget_key(chosen['holding_id'], chosen['units']))
        if st.button('赎回', key='wealth_redeem', disabled=flow.blocked):
            units_text = _units_from_input(units)
            if units_text is None:
                st.warning('赎回份额必须为正数且最多四位小数，不会自动截断或舍入。')
            else:
                perform(flow.start_action, 'wealth.redeem',
                        dict(account_id=chosen['account_id'], holding_id=chosen['holding_id'],
                             units=units_text))
    else:
        st.info('暂无持仓；申购成功后刷新可见。')

with ui.business_section('操作确认与回执', key=visual.card_key('core', 'wealth-receipt')):
    render(flow, action_prefix='wealth.')
