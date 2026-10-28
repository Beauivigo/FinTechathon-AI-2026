from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from datetime import date,datetime,time,timedelta,timezone
from app.workflow import current, render, perform
from app.money import yuan_text
from app.presentation import (apply_subscription_state, beijing, failure_note,
                              subscription_snapshot, subscription_state_label)

st.set_page_config(page_title='订阅管理',layout='wide')
from app import ui
from app import visual

# 先注入固定静态样式，再渲染页面内容；st.set_page_config 已在本文件最前面调用。
visual.apply('core')
from app.auth_ui import gate
gate()
ui.render_sidebar_nav('subscriptions')
with visual.band('core', 'subscriptions'):
    ui.render_page_header('订阅管理')
    ui.render_app_nav('subscriptions')

SNAPSHOT_KEY='subscription_snapshot'
REMINDER_STATES={'pending':'待提醒','sent':'已提醒','cancelled':'已取消','failed':'未完成'}

with visual.card('core', 'main'):
    st.info('候选与已知订阅来自本机资料。识别只生成候选，不扣费、不提醒；核实不新签授权，排除不取消扣费。')
    st.caption('自动识别：同商户≥2期、周期偏差≤3天、金额容差5%；不新增自动扣费授权。')
    flow = current()
    accounts = flow.resources['accounts']
    if accounts:
        account = st.selectbox('订阅账户',accounts,key='subscription_account',format_func=lambda a:a['display_name'])
        if st.button('扫描自动识别订阅',key='subscription_scan',disabled=flow.blocked):
            perform(flow.start_action,'subscription.scan',dict(account_id=account['account_id']))
        st.caption('订阅包含扣费资料，需要你确认授权读取本次列表；查询本身不扣款，也不授予代扣权限。')
        if st.button('申请查看订阅',key='subscription_list',disabled=flow.blocked):
            perform(flow.start_action,'subscription.list',dict(account_id=account['account_id']))
    if (flow.request and flow.request['action']=='subscription.scan' and flow.result
            and flow.result['status']=='succeeded'):
        st.success(f"扫描完成，本次新建候选 {flow.result['data']['created']} 个。请重新申请查看订阅。")
    current_account_id = account['account_id'] if accounts else None
    # Keep the confirmed read snapshot with its query time; later management
    # receipts update only the affected row, and a fresh read still needs a new
    # yellow confirmation.
    if (flow.request and flow.request['action']=='subscription.list' and flow.result
            and flow.result['status']=='succeeded' and not flow.needs_reconciliation):
        query_key = flow.request.get('idempotency_key', flow.request['request_id'])
        previous = st.session_state.get(SNAPSHOT_KEY)
        if not isinstance(previous, dict) or previous.get('query_key') != query_key:
            snapshot = subscription_snapshot(flow.result, queried_at=beijing(flow.gateway.clock().isoformat()))
            if snapshot is not None:
                snapshot['account_id'] = flow.request['params']['account_id']
                snapshot['query_key'] = query_key
                st.session_state[SNAPSHOT_KEY]=snapshot
    snapshot = st.session_state.get(SNAPSHOT_KEY)
    if flow.request and flow.result and flow.result['status']=='succeeded' and isinstance(snapshot,dict):
        data = flow.result['data'] or {}
        action = flow.request['action']
        subscription_id = data.get('subscription_id') or flow.request['params'].get('subscription_id')
        if action in {'subscription.review','subscription.cancel'} and data.get('subscription_state'):
            apply_subscription_state(snapshot, subscription_id, data['subscription_state'])
        elif action=='subscription.charge' and subscription_id:
            apply_subscription_state(snapshot, subscription_id, 'active',
                                     next_charge_date=data.get('next_charge_date'))
    if (isinstance(snapshot,dict) and isinstance(snapshot.get('rows'), list)
            and snapshot.get('account_id')==current_account_id):
        st.caption('已确认查询的订阅快照 · '+str(snapshot.get('queried_at','查询时点未知'))
                   +'；管理结果按账本回执更新，需要最新全量状态时请重新申请查看。')
        rows = snapshot['rows']
        if not rows:
            st.info('本次查询没有订阅记录。')
        ui.render_records([{'商户':r['merchant_name'], '金额':yuan_text(r['amount_minor']),
                       '下次扣费日':r['next_charge_date'],
                       '状态':subscription_state_label(r['subscription_state'])} for r in rows], hide_index=True,
                       title_field='商户', value_field='金额', meta_fields=('下次扣费日', '状态'),
                       key='subscription_summary_records', layout='tiles', tone='core', limit=None)
        with st.expander('订阅资料详情（编号与原始字段）'):
            st.dataframe([{'订阅编号':r['subscription_id'],'账户':snapshot['account_id'],
                           '商户':r['merchant_name'],'原始状态':r['subscription_state'],
                           '依据':r.get('evidence','')} for r in rows], hide_index=True)
        candidates = [s for s in rows if s['subscription_state']=='candidate']
        if candidates:
            selected = st.selectbox('选择候选',candidates,key='subscription_candidate',
                format_func=lambda s:s['merchant_name']+' · '+subscription_state_label(s['subscription_state']))
            st.write('候选依据：'+selected['evidence'])
            decision = st.selectbox('处理方式',['accept','reject'],key='subscription_decision',
                format_func=lambda d:'核实' if d=='accept' else '排除')
            if st.button('提交候选处理',key='subscription_review',disabled=flow.blocked):
                perform(flow.start_action,'subscription.review',dict(subscription_id=selected['subscription_id'],decision=decision))
        active=[s for s in rows if s['subscription_state']=='active']
        if active:
            chosen=st.selectbox('管理有效订阅',active,key='subscription_active',
                format_func=lambda s:s['merchant_name']+' · '+subscription_state_label(s['subscription_state']))
            if st.button('申请取消订阅',key='subscription_cancel',disabled=flow.blocked):
                perform(flow.start_action,'subscription.cancel',dict(subscription_id=chosen['subscription_id']))
            if st.button('本期续费（到期后可用）',key='subscription_charge',disabled=flow.blocked):
                perform(flow.start_action,'subscription.charge',dict(subscription_id=chosen['subscription_id']))
            default=date.fromisoformat(chosen['next_charge_date'])-timedelta(days=3)
            day=st.date_input('提醒日期',value=default,key='reminder_date_'+chosen['subscription_id'])
            hour=st.time_input('提醒时间（北京时间）',value=time(9),key='reminder_time')
            if st.button('申请登记提醒',key='subscription_remind',disabled=flow.blocked):
                perform(flow.start_action,'subscription.remind',
                    dict(subscription_id=chosen['subscription_id'],
                         remind_at=datetime.combine(day,hour,timezone(timedelta(hours=8))).isoformat()))
    else:
        st.caption('尚未读取订阅列表；扫描只会生成候选，查看列表仍需你确认授权。')
    if st.button('检查到期本地提醒',key='subscription_check_reminders',disabled=flow.blocked):
        st.session_state.reminder_snapshot=flow.gateway.run_reminders(principal_id=flow.principal_id)
        st.rerun()
    if st.session_state.get('reminder_snapshot'):
        st.caption('上次手动检查的本地提醒快照；未向微信、短信或其他外部服务发送通知。')
        ui.render_records([{'订阅':r['merchant_name'],'提醒时间':beijing(r['remind_at']),
                       '状态':REMINDER_STATES.get(r['state'],r['state'])}
                      for r in st.session_state.reminder_snapshot],hide_index=True,
                      title_field='订阅', meta_fields=('提醒时间', '状态'),
                      key='subscription_reminder_records', layout='activity', tone='core', limit=None)
    if flow.result and flow.result['status'] in {'failed','blocked'}:
        note = failure_note(flow.result)
        if note: st.warning(note)
    if (flow.request and flow.request['action']=='subscription.charge' and flow.result
            and flow.result['status']=='succeeded'):
        data=flow.result['data']
        st.success(f"已完成本期续费 {yuan_text(data['charged_minor'])}，下次扣费日 {data['next_charge_date']}。")
    render(flow, action_prefix='subscription.')
