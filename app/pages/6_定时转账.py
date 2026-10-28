from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform, demo_payees
from app.money import amount_input, parse_yuan, yuan_text
from app.presentation import beijing, failure_note, schedule_display
from app.background_panel import render_background
from bank.background import BackgroundController
from bank.scheduler import Scheduler
from shared import ServiceError, ContractError, adapt_request

st.set_page_config(page_title='预约转账',layout='wide')
from app import ui
from app import visual

# 先注入固定静态样式，再渲染页面内容；st.set_page_config 已在本文件最前面调用。
visual.apply('core')
from app.auth_ui import gate
gate()
ui.render_sidebar_nav('scheduled')
with visual.band('core', 'scheduled'):
    ui.render_page_header('预约转账')
    ui.render_app_nav('scheduled')

with visual.card('core', 'main'):
    st.info('预约只登记，不扣款、不冻结余额；到期后仍需你确认或身份验证，派发不等于付款。')
    st.caption('未领取任务迟到超过5分钟会失效；后台检查未启用时，可用下方按钮手动检查。')
    flow = current()
    scheduler = Scheduler(flow.gateway)
    local_zone = timezone(timedelta(hours=8))
    now = flow.gateway.clock().astimezone(local_zone)
    st.caption('服务端北京时间：'+now.strftime('%Y-%m-%d %H:%M:%S'))
    if getattr(flow.gateway, 'demo_mode', False):
        st.caption('当前为体验示例账户；体验模式不启动后台，可用“检查一次”手动推进到期任务。')
    else:
        try:
            background = BackgroundController(flow.gateway.store).status()
        except Exception:
            background = {}
        if background.get('running'):
            st.success('后台检查运行中；到期后仍会等待你确认或身份验证。')
        else:
            st.warning('后台检查当前未运行；预约不会自动准备，请到“后台任务与通知”启用，或稍后手动检查。')
    accounts = flow.resources['accounts']
    try:
        payees = demo_payees(flow)
    except (ServiceError, ContractError):
        st.error('联系人暂不可用。')
        render(flow, action_prefix='transfer.')
        st.stop()
    if accounts and payees:
        account = st.selectbox('付款账户',accounts,key='schedule_account',
            index=0 if len(accounts)==1 else None,format_func=lambda a:a['display_name'])
        payee = st.selectbox('收款人',payees,key='schedule_payee',index=None,placeholder='请选择',
            format_func=lambda p:p['display_name']+' · '+p['bank_name']+' · '+p['masked_account'])
        amount = amount_input('预约金额（元，最多两位小数）', 'schedule_amount')
        memo = st.text_input('备注（选填）', max_chars=50, key='schedule_memo')
        default = now+timedelta(minutes=5)
        day = st.date_input('执行日期（北京时间）',value=default.date(),key='schedule_day')
        at = st.time_input('执行时间（北京时间）',value=default.time().replace(second=0,microsecond=0,tzinfo=None),step=60,key='schedule_time')
        st.caption('保存前请核对：到期后需本人处理；付款是否完成以原执行结果为准。')
        if st.button('核对并保存预约',key='schedule_submit',disabled=flow.blocked or day is None or at is None):
            try:
                if account is None or payee is None:
                    raise ValueError('请选择付款账户和收款人。')
                amount_minor = parse_yuan(amount)
            except ValueError as exc:
                st.warning(str(exc))
            else:
                params = dict(account_id=account['account_id'],payee_id=payee['payee_id'],
                    amount_minor=amount_minor,execute_at=datetime.combine(day,at,local_zone).isoformat())
                if memo.strip():
                    params['memo'] = memo.strip()
                perform(flow.start_action,'transfer.schedule',params)
    st.divider()
    if st.button('检查一次到期任务',key='schedule_run'):
        perform(scheduler.run_once,principal_id=flow.principal_id)
    tasks = [t for t in scheduler.list_tasks(principal_id=flow.principal_id)
             if t['spec']['payload'].get('action') == 'transfer.create']
    for task in tasks:
        request = task['spec']['payload']
        try:
            query = adapt_request('operation.get', dict(original_action=request['action'],
                original_idempotency_key=request['idempotency_key']),
                request_id='view_'+uuid4().hex, trace_id=request['trace_id'])
            reply = flow.gateway.execute(query, principal_id=flow.principal_id)
        except (ServiceError, ContractError):
            task['operation_status'] = 'UNKNOWN'
        else:
            task['operation_status'] = (reply['data']['operation_result']['status']
                if reply['status']=='succeeded'
                else ('NOT_FOUND' if (reply.get('error') or {}).get('code')=='NOT_FOUND' else 'UNKNOWN'))
        task['display'], task['hint'] = schedule_display(task['state'], task.get('operation_status'))
    if tasks:
        ui.render_records([{'约定时间':beijing(t['spec']['run_at']),
                       '金额':yuan_text(t['spec']['payload']['params']['amount_minor']),
                       '状态':t['display']} for t in tasks],hide_index=True,
                       title_field='约定时间', value_field='金额', meta_fields=('状态',),
                       key='scheduled_transfer_records', layout='activity', tone='core', limit=None)
        with st.expander('预约编号与原始付款状态'):
            st.dataframe([{'预约编号':t['task_id'],'原付款操作编号':t['spec']['payload']['idempotency_key'],
                           '原付款状态':t.get('operation_status') or '未提供'} for t in tasks],hide_index=True)
        selected = st.selectbox('选择任务',tasks,key='schedule_task',
            format_func=lambda t:beijing(t['spec']['run_at'])+' · '+t['display'])
        st.caption('预约登记、派发和付款完成是不同状态；派发不等于已扣款。未完成或已取消的任务不会自动重新登记。')
        if selected.get('hint'):
            st.caption(selected['hint'])
        if selected['state']=='scheduled':
            confirmed = st.checkbox('确认取消这个尚未领取的任务',key='schedule_cancel_confirm')
            if st.button('取消选中预约',key='schedule_cancel',disabled=not confirmed):
                def cancel():
                    with flow.gateway.store.connection(write=True) as c:
                        flow.gateway.services.cancel_task(c,selected['task_id'],principal_id=flow.principal_id)
                perform(cancel)
            st.caption('取消预约只停止尚未领取的任务，不代表退款；已发生的付款不会因此撤销。')
        operation_status = selected.get('operation_status')
        needs_payment_action = (selected['state'] in ('running','unknown')
            or operation_status in {'awaiting_confirmation','awaiting_verification',
                                    'unknown','running','UNKNOWN'})
        if needs_payment_action:
            if st.button('查询并恢复此任务的付款操作',key='schedule_execution'):
                perform(flow.recover,action='transfer.create',
                        idempotency_key=selected['spec']['payload']['idempotency_key'])
            st.caption('恢复后仍按原确认或身份验证办理，不会换键重新付款。')
    render_background(flow)
    render(flow, action_prefix='transfer.')
