"""模拟理财：风险问卷、产品对比、申购、持仓与赎回。页面不自行计算费用/份额。"""
from pathlib import Path
import sys
from decimal import Decimal, InvalidOperation
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform


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


st.set_page_config(page_title='模拟理财', layout='wide')
st.title('理财操作')
st.caption('以下为 D 第一轮定版的虚构产品，不承诺收益；页面只展示服务端结果，金额以整数分提交。')

flow = current()
resources = flow.resources
accounts = resources['accounts']
products = resources['products']
holdings = resources.get('holdings', [])
if not accounts:
    st.error('当前用户没有可用账户。')
    st.stop()
account = st.selectbox('账户', accounts, format_func=lambda a: a['display_name'], key='wealth_account')

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
        choice = st.selectbox(label, ['a', 'b', 'c', 'd'], key='answer_' + question,
                              format_func=lambda x,q=question: options[q]['abcd'.index(x)])
        answers[question] = f'{question}_{choice}'
    if st.button('提交风险评估', key='wealth_assess', disabled=flow.blocked):
        perform(flow.start_action, 'wealth.assess',
                dict(questionnaire_version='wpq_v1', answers=answers))

if flow.request and flow.request['action'] == 'wealth.assess' and flow.result \
        and flow.result['status'] == 'succeeded':
    st.session_state['risk_profile_id'] = flow.result['data']['risk_profile_id']
    st.success(f"风险评估完成：{flow.result['data']['risk_level']}（档案 {flow.result['data']['risk_profile_id']}）")

profile_id = st.session_state.get('risk_profile_id')
if products:
    product = st.selectbox('产品', products, key='wealth_product',
                           format_func=lambda p: f"{p['product_id']} · {p['name']} · {p['risk_level']} · 起购 {p['minimum_amount_minor'] // 100} 元")
    amount_yuan = st.text_input('申购金额（元）', value='100.00', key='wealth_amount')
    if st.button('申购', key='wealth_purchase', disabled=flow.blocked):
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

st.subheader('当前持仓')
if holdings:
    st.dataframe([{'持仓': h['holding_id'], '产品': h['product_id'], '份额': h['units']} for h in holdings],
                 hide_index=True)
    chosen = st.selectbox('选择持仓', holdings, key='wealth_holding',
                          format_func=lambda h: f"{h['holding_id']} · {h['product_id']} · {h['units']}")
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

render(flow)
