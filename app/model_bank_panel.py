"""模型草稿到原银行工作流的可信接线，确认与验证由原界面执行。"""
from copy import deepcopy
import streamlit as st
from agent.controller import Conversation, reconcile_session_flows
from agent.model_dialogue import DialogueSession
from agent.model_runtime import TrustedModelCaller
from agent.model_bank import BankPlanRunner, NeedsInput
from app.model_view import render_model_dialogue
from app import demo
from app.workflow import render as render_bank_operation
from shared import ContractError, ServiceError
from shared.contracts import ACTIONS
from bank.plan_store import PlanStore


def _render_authoritative_request(flow):
    if not flow.request:return True
    p=flow.request['params']
    labels={'account_id':'业务账户','payee_id':'收款对象编号','card_id':'银行卡编号','subscription_id':'订阅编号',
        'product_id':'理财产品编号','risk_profile_id':'风险档案编号','holding_id':'持仓编号','units':'赎回份额',
        'reservation_id':'预留编号','item_id':'商品编号','deliver_on':'配送日期','delivery_address_id':'地址编号',
        'execute_at':'预约执行时间','remind_at':'提醒时间','purpose':'预留用途','memo':'转账备注',
        'questionnaire_version':'问卷版本','answers':'问卷答案','decision':'核实结论','payee_ids':'AA参与人编号'}
    with st.expander('核对本次银行操作',expanded=flow.pending):
        for key,value in p.items():
            if key in {'amount_minor','limit_minor'}:
                st.text(('本笔金额' if key=='amount_minor' else '单笔限额')+f'：{value//100}.{value%100:02d}元')
            elif key in labels:st.text(labels[key]+'：'+str(value))
        if flow.request['action'] in {'transfer.create','transfer.schedule'}:
            try:person=flow.gateway.operation_payee(flow.request,principal_id=flow.principal_id)
            except (ServiceError,ContractError):person=None
            if person:
                st.text('收款人：'+person['display_name']+' · '+person['masked_account'])
            elif flow.pending:
                st.warning('收款信息暂不可核实，请先查询原操作，暂不允许确认。')
                if st.button('核实原转账',key='model_reconcile_payee'):
                    flow.recover();st.rerun()
                return False
    return True


def render_model_bank():
    if 'model_bank_flow' not in st.session_state:
        flow=Conversation(demo.gateway(),principal_id=demo.PRINCIPAL)
        caller=TrustedModelCaller(flow)
        st.session_state.model_bank_flow=flow
        st.session_state.model_caller=caller
        st.session_state.model_dialogue=DialogueSession(caller,
            audit=lambda event:flow.gateway.record_agent_event(event,principal_id=flow.principal_id))
    flow=st.session_state.model_bank_flow
    reconcile_session_flows(st.session_state, flow)
    flow.refresh_operations()
    from app.background_panel import render_background
    render_background(flow)
    caller=st.session_state.model_caller
    session=st.session_state.model_dialogue
    journal=PlanStore(flow.gateway.store)
    with st.expander('恢复完整计划'):
        plans=journal.list(flow.principal_id)
        if plans:
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
        else:st.caption('尚无已保存计划。')
    runner=st.session_state.get('model_runner')
    if runner:
        try:runner._sync()
        except NeedsInput as exc:st.warning(str(exc))
        caller.observations=deepcopy(runner.receipts)
    wrote=bool(runner and any(r['status']=='succeeded' and ACTIONS.get(t.get('bank_action'),{}).get('write') for t in runner.tasks for k,r in runner.receipts.items() if k==t['id']))
    snap=render_model_dialogue(session,blocked=flow.blocked,allow_back=not wrote)
    st.caption('“新需求”只清空当前草稿；已启用后台的保存计划请先暂停或人工接管，不会随清空自动取消。')
    if caller.last_error:st.warning(caller.last_error)
    proposal=snap['proposal']
    if snap['phase'] in {'proposal','plan'} and proposal and st.session_state.get('model_runner_version')!=snap['version']:
        if proposal['kind']=='single':
            tasks=[{'id':'t1','title':'当前需求','intent':proposal['intent'],'fields':proposal['fields'],'depends_on':[]}]
        else:tasks=proposal['tasks']
        try:
            candidate=BankPlanRunner(flow,tasks,trace_id=session.trace_id,audit=session.audit,journal=journal)
            if runner and runner.trace_id == session.trace_id:
                candidate.inherit_completed(runner)
            candidate.checkpoint('revision')
            runner=candidate
            st.session_state.model_runner=runner
            st.session_state.model_runner_version=snap['version']
        except NeedsInput as exc:
            st.warning(str(exc))
            if runner: runner.paused=True
            return
    elif snap['phase'] not in {'proposal','plan','paused','handoff'}:
        st.session_state.pop('model_runner',None);runner=None
    if runner:
        runner.paused=runner.integrity_blocked or snap['phase'] in {'paused','handoff'} or any(t['status']=='stopped' for t in runner.tasks)
        if st.button('允许后台准备后续待办（付款仍需授权）',key='plan_automation'):
            runner.automation=True
        try:runner.checkpoint('control')
        except NeedsInput as exc:st.warning(str(exc));return
        issue=None
        try:
            # Only pure green queries advance on a rerun. Receipts prevent replay.
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
            st.caption('下一步：'+pending['title']+'。准备后仍须按银行要求确认或模拟强验证。')
            if st.button('核对并准备下一步',key='model_prepare_step'):
                try:
                    runner.advance(allow_write=True)
                    st.rerun()
                except (NeedsInput,ValueError,ContractError,ServiceError) as exc:
                    st.warning(str(exc) if isinstance(exc,NeedsInput) else '操作未通过银行校验，请补充或核对参数。')
        labels={'pending':'尚未执行','awaiting_bank':'待银行确认或核实','completed':'已完成','stopped':'已停止'}
        st.dataframe([{'步骤':t['id'],'任务':t['title'],'先完成':','.join(t['depends_on']) or '无','状态':labels[t['status']]} for t in runner.tasks],hide_index=True)
        if runner.receipts:
            st.subheader('银行端实际回执')
            for key,result in runner.receipts.items():
                with st.expander(key+' · '+result['status'],expanded=True):
                    st.json(result.get('data',result.get('error',{})))
            st.caption('这些结果来自本地模拟银行；模型的草稿不是资金变动证据。')
        if wrote:st.caption('已有业务回执，不能用回退草稿撤销已提交操作；需要更正时先人工核对。')
    if _render_authoritative_request(flow):render_bank_operation(flow)
    if caller.last_metadata:
        source={'desktop':'桌宠本机配置','configured':'独立配置','local':'本地模型'}
        st.caption('最近模型调用：'+caller.last_metadata.get('model','')+'；来源：'+source.get(caller.last_metadata.get('credential_source'),'已配置'))
