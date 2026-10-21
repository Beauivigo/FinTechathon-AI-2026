from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform, demo_payees
from bank.scheduler import Scheduler
from shared import ServiceError, ContractError

st.set_page_config(page_title='模拟定时转账',layout='wide')
from app import ui

ui.render_sidebar_nav('scheduled')
ui.render_page_header('定时转账')
ui.render_app_nav('scheduled')

with st.container(border=True):
    st.info('登记不扣款、不冻结余额。到期后重新确认或模拟验证，付款结果单独查询。')
    st.caption('本地演示需手动检查到期任务；程序关闭时不会自动运行。未领取任务迟到超过5分钟即失效。')
    flow = current()
    scheduler = Scheduler(flow.gateway)
    local_zone = timezone(timedelta(hours=8))
    now = flow.gateway.clock().astimezone(local_zone)
    st.caption('服务端北京时间：'+now.strftime('%Y-%m-%d %H:%M:%S'))
    accounts = flow.resources['accounts']
    try:
        payees = demo_payees(flow)
    except (ServiceError, ContractError):
        st.error('模拟联系人暂不可用。')
        render(flow)
        st.stop()
    if accounts and payees:
        account = st.selectbox('付款账户',accounts,key='schedule_account',format_func=lambda a:a['display_name'])
        payee = st.selectbox('收款人',payees,key='schedule_payee',format_func=lambda p:p['display_name']+' · '+p['masked_account'])
        amount = st.number_input('金额（整数分）',min_value=1,value=20000,step=1,key='schedule_amount')
        default = now+timedelta(minutes=5)
        day = st.date_input('执行日期（北京时间）',value=default.date(),key='schedule_day')
        at = st.time_input('执行时间（北京时间）',value=default.time().replace(second=0,microsecond=0,tzinfo=None),step=60,key='schedule_time')
        if st.button('提交定时登记',key='schedule_submit',disabled=flow.blocked or day is None or at is None):
            perform(flow.start_action,'transfer.schedule',dict(account_id=account['account_id'],payee_id=payee['payee_id'],
                amount_minor=amount,execute_at=datetime.combine(day,at,local_zone).isoformat()))
    st.divider()
    if st.button('检查一次到期任务',key='schedule_run'):
        perform(scheduler.run_once,principal_id=flow.principal_id)
    tasks = [t for t in scheduler.list_tasks(principal_id=flow.principal_id)
             if t['spec']['payload'].get('action') == 'transfer.create']
    if tasks:
        st.dataframe([dict(任务=t['task_id'],约定时间=t['spec']['run_at'],状态=t['state'],
            金额分=t['spec']['payload']['params']['amount_minor']) for t in tasks],hide_index=True)
        selected = st.selectbox('选择任务',tasks,key='schedule_task',format_func=lambda t:t['spec']['run_at']+' · '+t['state']+' · '+t['task_id'])
        st.caption('dispatched仅表示已派发，实际付款以原执行结果为准。failed或cancelled不会自动重新登记。')
        if selected['state']=='scheduled':
            confirmed = st.checkbox('确认取消这个尚未领取的任务',key='schedule_cancel_confirm')
            if st.button('取消选中任务',key='schedule_cancel',disabled=not confirmed):
                def cancel():
                    with flow.gateway.store.connection(write=True) as c:
                        flow.gateway.services.cancel_task(c,selected['task_id'],principal_id=flow.principal_id)
                perform(cancel)
        if selected['state'] in ('running','unknown','dispatched'):
            if st.button('查询并恢复此任务的付款操作',key='schedule_execution'):
                perform(flow.recover,action='transfer.create',idempotency_key=selected['spec']['payload']['idempotency_key'])
    render(flow)
