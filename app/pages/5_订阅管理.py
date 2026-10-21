from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from datetime import date,datetime,time,timedelta,timezone
from app.workflow import current, render, perform

st.set_page_config(page_title='模拟订阅管理',layout='wide')
from app import ui

ui.render_sidebar_nav('subscriptions')
ui.render_page_header('订阅核实与排除')
ui.render_app_nav('subscriptions')

with st.container(border=True):
    st.info('固定候选与已知订阅来自 E 资料；可按流水自动识别周期扣费，识别只生成候选，不扣费、不提醒。核实不新签授权，排除不取消扣费。')
    st.caption('自动识别依据 E-03：同商户≥2 期、周期偏差≤3 天、金额容差 5%；可取消有效订阅并登记本地续费提醒；不新增自动扣费授权。')
    flow = current()
    accounts = flow.resources['accounts']
    if accounts:
        account = st.selectbox('订阅账户',accounts,key='subscription_account',format_func=lambda a:a['display_name'])
        if st.button('扫描自动识别订阅',key='subscription_scan',disabled=flow.blocked):
            perform(flow.start_action,'subscription.scan',dict(account_id=account['account_id']))
        if st.button('申请查看订阅',key='subscription_list',disabled=flow.blocked):
            perform(flow.start_action,'subscription.list',dict(account_id=account['account_id']))
    if (flow.request and flow.request['action']=='subscription.scan' and flow.result
            and flow.result['status']=='succeeded'):
        st.success(f"扫描完成，本次新建候选 {flow.result['data']['created']} 个。请重新申请查看订阅。")
    if (flow.request and flow.request['action']=='subscription.list' and flow.result
            and flow.result['status']=='succeeded' and not flow.needs_reconciliation):
        rows = flow.result['data']['subscriptions']
        st.caption('本次已确认查询的快照；修改或需要最新状态时请重新申请查看。')
        st.dataframe(rows,hide_index=True)
        candidates = [s for s in rows if s['subscription_state']=='candidate']
        if candidates:
            selected = st.selectbox('选择候选',candidates,key='subscription_candidate',format_func=lambda s:s['merchant_name']+' · '+s['subscription_id'])
            st.write('候选依据：'+selected['evidence'])
            decision = st.selectbox('处理方式',['accept','reject'],key='subscription_decision',format_func=lambda d:'核实' if d=='accept' else '排除')
            if st.button('提交候选处理',key='subscription_review',disabled=flow.blocked):
                perform(flow.start_action,'subscription.review',dict(subscription_id=selected['subscription_id'],decision=decision))
        active=[s for s in rows if s['subscription_state']=='active']
        if active:
            chosen=st.selectbox('管理有效订阅',active,key='subscription_active',format_func=lambda s:s['merchant_name'])
            if st.button('申请取消订阅',key='subscription_cancel',disabled=flow.blocked):
                perform(flow.start_action,'subscription.cancel',dict(subscription_id=chosen['subscription_id']))
            if st.button('本期续费（到期后可用）',key='subscription_charge',disabled=flow.blocked):
                perform(flow.start_action,'subscription.charge',dict(subscription_id=chosen['subscription_id']))
            default=date.fromisoformat(chosen['next_charge_date'])-timedelta(days=3)
            day=st.date_input('提醒日期',value=default,key='reminder_date_'+chosen['subscription_id'])
            hour=st.time_input('提醒时间（北京时间）',value=time(9),key='reminder_time')
            if st.button('申请登记提醒',key='subscription_remind',disabled=flow.blocked):
                perform(flow.start_action,'subscription.remind',dict(subscription_id=chosen['subscription_id'],remind_at=datetime.combine(day,hour,timezone(timedelta(hours=8))).isoformat()))
    if st.button('检查到期本地提醒',key='subscription_check_reminders',disabled=flow.blocked):
        st.session_state.reminder_snapshot=flow.gateway.run_reminders(principal_id=flow.principal_id)
        st.rerun()
    if st.session_state.get('reminder_snapshot'):
        st.caption('上次手动检查的本地提醒快照；未向微信、短信或其他外部服务发送通知。')
        st.dataframe([{'订阅':r['merchant_name'],'提醒时间':r['remind_at'],'状态':r['state']} for r in st.session_state.reminder_snapshot],hide_index=True)
    if (flow.request and flow.request['action']=='subscription.charge' and flow.result
            and flow.result['status']=='succeeded'):
        data=flow.result['data']
        st.success(f"已完成本期续费 {data['charged_minor']} 分，下次扣费日 {data['next_charge_date']}。")
    render(flow)
