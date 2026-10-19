"""模拟理财：风险问卷、产品对比、申购、持仓与赎回。页面不自行计算费用/份额。"""
from pathlib import Path
import sys
from decimal import Decimal
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform

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
    answers = {}
    for question, label in labels.items():
        choice = st.selectbox(label, ['a', 'b', 'c', 'd'], key='answer_' + question,
                              format_func=lambda x: {'a': '选项A（1分）', 'b': '选项B（2分）',
                                                     'c': '选项C（3分）', 'd': '选项D（4分）'}[x])
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
    amount_yuan = st.number_input('申购金额（元）', min_value=1.0, value=100.0, step=1.0, key='wealth_amount')
    if st.button('申购', key='wealth_purchase', disabled=flow.blocked):
        if not profile_id:
            st.warning('请先完成风险问卷。')
        elif not account:
            st.warning('请选择账户。')
        else:
            perform(flow.start_action, 'wealth.purchase',
                    dict(account_id=account['account_id'], product_id=product['product_id'],
                         risk_profile_id=profile_id,
                         amount_minor=int(Decimal(str(amount_yuan)) * 100)))

st.subheader('当前持仓')
if holdings:
    st.dataframe([{'持仓': h['holding_id'], '产品': h['product_id'], '份额': h['units']} for h in holdings],
                 hide_index=True)
    chosen = st.selectbox('选择持仓', holdings, key='wealth_holding',
                          format_func=lambda h: f"{h['holding_id']} · {h['product_id']} · {h['units']}")
    units = st.number_input('赎回份额', min_value=0.0001, value=float(Decimal(chosen['units'])),
                            step=0.0001, format='%.4f', key='wealth_units')
    if st.button('赎回', key='wealth_redeem', disabled=flow.blocked):
        perform(flow.start_action, 'wealth.redeem',
                dict(account_id=chosen['account_id'], holding_id=chosen['holding_id'],
                     units=format(Decimal(str(units)).quantize(Decimal('0.0001')), 'f')))
else:
    st.info('暂无持仓；申购成功后刷新可见。')

render(flow)
