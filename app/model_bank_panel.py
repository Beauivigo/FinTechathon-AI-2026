"""模型草稿到原银行工作流的可信接线，确认与验证由原界面执行。"""
from copy import deepcopy
import streamlit as st
from agent.controller import Conversation, reconcile_session_flows
from agent.model_dialogue import DialogueSession
from agent.model_runtime import TrustedModelCaller
from agent.model_bank import BankPlanRunner, NeedsInput
from app.model_view import render_model_dialogue
from app import demo, ui
from app.workflow import render as render_bank_operation
from shared import ContractError, ServiceError
from shared.contracts import ACTIONS
from bank.plan_store import PlanStore
from bank.birthday import schedule_ready, tasks_from_slots
from uuid import uuid4
from app.presentation import (STATES, beijing, failure_note, model_error_guidance,
                             render_authorization, receipt_details)
from app import visual


def _restorable_plans(journal, flow):
    """Hide journals that only record a completed simple read.

    A one-step green observation needs no restore/automation controls; real
    multi-step or unfinished plans stay recoverable.
    """
    simple = {'account.balance', 'bill.analyze', 'wealth.compare', 'scenario.plan'}
    rows = []
    for row in journal.list(flow.principal_id):
        try:
            snapshot, _ = journal.load(row['plan_id'], flow.principal_id)
        except Exception:
            rows.append(row)
            continue
        tasks = snapshot.get('tasks') or []
        if (len(tasks) == 1 and tasks[0].get('status') == 'completed'
                and (tasks[0].get('bank_action') in simple or not tasks[0].get('bank_action'))):
            continue
        rows.append(row)
    return rows


def render_model_bank():
    if 'model_bank_flow' not in st.session_state:
        flow=Conversation(demo.gateway(),principal_id=demo.PRINCIPAL)
        caller=TrustedModelCaller(flow)
        st.session_state.model_bank_flow=flow
        st.session_state.model_caller=caller
        st.session_state.model_dialogue=DialogueSession(caller,
            audit=lambda event:flow.gateway.record_agent_event(event,principal_id=flow.principal_id))
    flow=st.session_state.model_bank_flow
    # 别的页面可能已确认/拒绝/过期收口原操作；先对齐权威记录再判断阻塞。
    flow.settle()
    reconcile_session_flows(st.session_state, flow)
    flow.refresh_operations()
    flow.resources=flow.gateway.get_context(principal_id=flow.principal_id)
    from app.background_panel import render_background
    caller=st.session_state.model_caller
    session=st.session_state.model_dialogue
    journal=PlanStore(flow.gateway.store)
    plans=_restorable_plans(journal, flow)
    if plans:
        render_background(flow)
    if plans:
        with st.expander('恢复已保存计划'):
            selected=st.selectbox('选择已保存计划',plans,format_func=lambda r:r['updated_at']+' · '+r['plan_id'],key='saved_plan')
            if st.button('恢复计划与进度',key='restore_plan'):
                if flow.request and (flow.pending or flow.needs_reconciliation) and flow.request['trace_id']!=selected['plan_id']:
                    st.warning('请先处理当前原操作，再切换其他计划。')
                else:
                    try:
                        restored=BankPlanRunner.restore(flow,journal,selected['plan_id'],audit=session.audit)
                        session.trace_id=restored.trace_id;session.version+=1
                        session.history=[];session.checkpoints=[]
                        session._state=session._empty()|dict(phase='paused' if restored.paused else 'plan',tasks=deepcopy(restored.tasks),
                            proposal={'kind':'plan','tasks':deepcopy(restored.tasks)},reply='已从数据库恢复计划；已完成业务不会重新执行。')
                        session._before_pause='plan'
                        st.session_state.model_runner=restored;st.session_state.model_runner_version=session.version
                        st.rerun()
                    except (NeedsInput,ValueError,ContractError,ServiceError):st.warning('原计划暂不可恢复，请保留编号核实。')
            st.caption('恢复只读取原编号与回执，不会自动确认或重新付款。')
    runner=st.session_state.get('model_runner')
    if runner:
        try:runner._sync()
        except NeedsInput as exc:st.warning(str(exc))
        caller.observations=deepcopy(runner.receipts)
    wrote=bool(runner and any(r['status']=='succeeded' and ACTIONS.get(t.get('bank_action'),{}).get('write')
        for t in runner.tasks for key,r in runner.receipts.items() if key==t['id']))
    if flow.blocked:
        from app import pending_view
        pending_view.render_unresolved(flow,key_prefix='model_pending',include_local=False,
            note='仍可继续提问、补充和修改草稿；新的业务提交需先处理或核实上方待办，模型不代替你确认或验证。')
    with visual.card('ai', 'dialogue'):
        snap=render_model_dialogue(session,blocked=flow.blocked,allow_back=not wrote)
    st.caption('“新需求”只清空当前草稿与输入；已启用后台的保存计划请先暂停或改为手动处理，不会随清空自动取消。')
    if caller.last_error:
        st.warning(model_error_guidance(caller.last_error))
        st.caption('可在“我的—高级设置”修改模型配置；已提交的银行操作仍以原回执为准。')
    proposal=snap['proposal']
    if (snap['phase'] in {'proposal','plan'} and proposal
            and st.session_state.get('model_runner_version')!=snap['version']
            and st.session_state.get('model_revision_rejected')!=snap['version']):
        birthday_plan=False
        try:
            if proposal['kind']=='single':
                fields=proposal['fields']
                if proposal['intent']=='birthday' and schedule_ready(fields):
                    tasks=tasks_from_slots(flow,fields,previous=runner if runner and runner.trace_id==session.trace_id else None)
                    birthday_plan=True
                else:
                    tasks=[{'id':'t1','title':'当前需求','intent':proposal['intent'],'fields':fields,'depends_on':[]}]
            else:
                tasks=proposal['tasks']
                if (len(tasks)==1 and tasks[0].get('intent')=='birthday'
                        and schedule_ready(tasks[0].get('fields',{}))):
                    # A one-task birthday plan is the same trusted draft as a
                    # single proposal; expand it into timed reserve/order/release steps.
                    tasks=tasks_from_slots(flow,tasks[0]['fields'],previous=runner if runner and runner.trace_id==session.trace_id else None)
                    birthday_plan=True
            if flow.blocked and runner and runner.journal:
                # A separate draft/query cannot overwrite the persisted plan
                # that owns the original pending or unknown bank operation.
                session.trace_id='plan_'+uuid4().hex
            candidate=BankPlanRunner(flow,tasks,trace_id=session.trace_id,audit=session.audit,journal=journal)
            if runner and runner.trace_id == session.trace_id:
                candidate.inherit_completed(runner)
            if birthday_plan:candidate.automation=True
            candidate.checkpoint('revision')
            runner=candidate
            st.session_state.model_runner=runner
            st.session_state.model_runner_version=snap['version']
            st.session_state.pop('model_revision_rejected',None)
        except NeedsInput as exc:
            st.warning(str(exc))
            st.session_state.model_revision_rejected=snap['version']
        except (ValueError,ContractError,ServiceError):
            st.warning('计划未保存，请核对生日、预算、商品、地址与时间；原计划保持不变。')
            st.session_state.model_revision_rejected=snap['version']
    elif snap['phase'] not in {'proposal','plan','paused','handoff'}:
        st.session_state.pop('model_runner',None);runner=None
    if runner:
        runner.paused=runner.integrity_blocked or snap['phase'] in {'paused','handoff'} or any(t['status']=='stopped' for t in runner.tasks)
        multi_step=len(runner.tasks)>1
        if multi_step and not flow.blocked and st.button('允许后台准备后续待办（付款仍需授权）',key='plan_automation'):
            runner.automation=True
        try:runner.checkpoint('control')
        except NeedsInput as exc:st.warning(str(exc));return
        issue=None
        try:
            # Pure observations may proceed without replacing the original
            # request; advance still blocks every new conflicting write.
            for _ in runner.tasks:
                if runner.advance() is None:break
        except (NeedsInput,ValueError,ContractError,ServiceError) as exc:
            issue=exc
            st.warning(str(exc) if isinstance(exc,NeedsInput) else '请核对参数；操作未通过银行校验。')
        pending=next((t for t in runner.tasks if t['status']=='pending'),None)
        if isinstance(issue,NeedsInput) and issue.choices and pending:
            choice=st.selectbox('请选择同名收款人的具体账户',issue.choices,
                format_func=lambda p:p['display_name']+' · '+p['masked_account'],key='model_payee_choice')
            if st.button('选用此收款账户',key='model_pick_payee'):
                runner.choices[pending['id']]=choice['payee_id'];st.rerun()
        if pending and not runner.paused and not flow.blocked:
            with visual.card('ai', 'step'):
                st.subheader('当前步骤')
                st.write('下一步：'+pending['title'])
                st.caption('准备后仍须按银行要求确认或验证；核对前不会执行。')
                if st.button('核对并准备下一步',key='model_prepare_step',type='primary',width='stretch'):
                    try:
                        runner.advance(allow_write=True)
                        st.rerun()
                    except (NeedsInput,ValueError,ContractError,ServiceError) as exc:
                        st.warning(str(exc) if isinstance(exc,NeedsInput) else '操作未通过银行校验，请补充或核对参数。')
        elif pending and flow.blocked:
            st.info('原操作未结束前不会准备新的付款步骤；可继续对话或先处理上方待办。')
        titles={t['id']:t['title'] for t in runner.tasks}
        labels={'pending':'尚未执行','awaiting_bank':'待本人处理或核实','completed':'已完成','stopped':'已停止'}
        ui.render_records([{'任务':t['title'],'先完成':'、'.join(titles.get(x,x) for x in t['depends_on']) or '无',
            '生效时间':'立即' if not t.get('not_before') else beijing(t['not_before']),
            '状态':labels[t['status']]} for t in runner.tasks], title_field='任务',
            meta_fields=('先完成', '生效时间', '状态'), limit=None,
            layout='steps', tone='ai', hide_index=True)
        with st.expander('任务编号与后台准备说明'):
            st.dataframe([{'步骤':t['id'],'编号':t.get('operation_key') or '尚未准备'} for t in runner.tasks],hide_index=True)
            st.caption('后台只会在生效时间之后准备待办；预算仍需确认，订单仍需身份验证，剩余释放也需确认。')
        visible_receipts=[]
        for key,result in list(runner.receipts.items())[-4:]:
            task=next((t for t in runner.tasks if t['id']==key),{})
            request=task.get('request')
            current=flow.request
            if (request and current and request['action']==current['action']
                    and request.get('idempotency_key')==current.get('idempotency_key')):
                continue  # The current operation card below already shows it.
            visible_receipts.append((key,result,task,request))
        if visible_receipts:
            st.subheader('银行回执')
            for key,result,task,request in visible_receipts:
                line=task.get('title',key)+'：'+STATES.get(result['status'],result['status'])
                note=failure_note(result)
                if note and result['status']!='succeeded':
                    line+=' · '+note
                st.text(line)
                if request:
                    for detail in receipt_details(request,result):
                        st.text(detail)
                elif task.get('intent')=='wealth_holdings' and result['status']=='succeeded':
                    st.text('本次查询持仓：'+str(len(result['data']['holdings']))+'项')
                with st.expander('查看回执详情'):
                    render_authorization(flow.gateway, task.get('request'), principal_id=flow.principal_id)
                    st.json(result.get('data',result.get('error',{})))
            st.caption('结果来自本地账本；模型草稿不是资金变动证据。')
        if wrote:st.caption('已有业务回执，不能用回退草稿撤销已提交操作；需要更正时先人工核对。')
    render_bank_operation(flow)
    if caller.last_metadata:
        source={'desktop':'桌宠本机配置','configured':'独立配置','local':'本地模型'}
        st.caption('最近模型调用：'+caller.last_metadata.get('model','')+'；来源：'+source.get(caller.last_metadata.get('credential_source'),'已配置'))
