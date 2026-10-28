from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform
from app.money import amount_input, parse_yuan, yuan_text
from shared import ServiceError

st.set_page_config(page_title='预算管理', layout='wide')
from app import ui
from app import visual

# 先注入固定静态样式，再渲染页面内容；st.set_page_config 已在本文件最前面调用。
visual.apply('bill')
from app.auth_ui import gate
gate()
ui.render_sidebar_nav('budget')
with visual.band('bill', 'budget'):
    ui.render_page_header('预算预留与释放')
    ui.render_app_nav('budget')

with visual.card('bill', 'main'):
    st.info('预留不付款，释放不产生退款流水；未知订单对应金额继续保留。')
    flow = current()
    accounts = flow.resources['accounts']
    if accounts:
        with st.form('budget_form'):
            account = st.selectbox('预留账户', accounts, index=0 if len(accounts)==1 else None,
                                   placeholder='请选择账户', key='budget_account', format_func=lambda a:a['display_name'])
            amount = amount_input('预留金额（元，最多两位小数）', 'budget_amount')
            purpose = st.text_input('用途', key='budget_purpose')
            st.caption('请填写用途和元金额；确认后减少可用余额，账面余额不变，未发生付款。')
            if st.form_submit_button('核对并预留预算', key='budget_reserve', disabled=flow.blocked):
                try:
                    if account is None or not purpose.strip():
                        raise ValueError('请选择账户并填写预留用途。')
                    amount_minor = parse_yuan(amount)
                except ValueError as exc:
                    st.warning(str(exc))
                else:
                    perform(flow.start_action, 'budget.reserve', dict(account_id=account['account_id'], amount_minor=amount_minor, purpose=purpose.strip()))
    try:
        rows = flow.gateway.list_reservations(principal_id=flow.principal_id)
    except ServiceError:
        st.error('预算列表暂不可用，请先核实原操作。')
        st.stop()
    if rows:
        state_labels = {'active':'有效','partially_consumed':'部分使用','frozen':'已冻结','released':'已释放','closed':'已结束'}
        ui.render_records([{'预留编号': r['reservation_id'], '账户': r['account_id'], '用途': r['purpose'],
                       '预留金额（元）': yuan_text(r['amount_minor']),
                       '剩余预留（元）': yuan_text(r['remaining_reserved_minor']),
                       '状态': state_labels.get(r['state'], r['state'])} for r in rows], hide_index=True,
                       title_field='用途', value_field='剩余预留（元）',
                       meta_fields=('预留金额（元）', '状态'), key='budget_reservation_records',
                       layout='tiles', tone='bill', limit=None,
                       progress_values=[r['remaining_reserved_minor']/r['amount_minor']
                                        if r['amount_minor'] else None for r in rows])
        active = [r for r in rows if r['state'] in ('active','partially_consumed')]
        if active:
            selected = st.selectbox('选择要释放的预留', active, key='budget_release_selection',
                                    format_func=lambda r:f"{r['purpose']} · 剩余预留 {yuan_text(r['remaining_reserved_minor'])}")
            if st.button('核对可释放余额', key='budget_release', disabled=flow.blocked):
                perform(flow.start_action, 'budget.release', dict(reservation_id=selected['reservation_id']))
            st.caption('释放只退回可用余额中未被消费的预留，不是退款；待核实订单占用金额继续保留。')
    render(flow, action_prefix='budget.')
