"""Manage a persisted plan using its original requests and bank receipts."""
import streamlit as st
from agent.model_bank import BankPlanRunner, NeedsInput
from bank.plan_store import PlanStore
from shared import ServiceError, ContractError
from app.presentation import beijing, STATES
from app import ui


def render_saved_plan(flow, *, key_prefix='birthday_progress'):
    journal = PlanStore(flow.gateway.store)
    plans = journal.list(flow.principal_id)
    from app.pending_view import render_unresolved
    # The original pending operation must be reachable even when no full plan
    # exists and the page's input form is empty after navigation. When this
    # page already holds the original request, the shared operation area below
    # shows it once, so avoid a duplicated control block.
    if not flow.request:
        render_unresolved(flow, key_prefix=key_prefix+'_pending',
            note='这里处理原待办；确认、取消和查询原操作都不依赖重新填写生日或地址。')
    if not plans:
        st.caption('尚无已保存计划。保存后可在这里恢复，不需要去 AI 页面查找编号。')
        return
    st.subheader('已保存计划与进度')
    def title(row):
        snapshot, _ = journal.load(row['plan_id'], flow.principal_id)
        return '、'.join(t['title'] for t in snapshot['tasks'][:2])+' · '+beijing(row['updated_at'])
    preferred = st.session_state.pop('birthday_open_plan', None)
    if preferred:
        st.session_state[key_prefix+'_select'] = next((p for p in plans if p['plan_id']==preferred), plans[0])
    selected = st.selectbox('选择已保存计划', plans, format_func=title, key=key_prefix+'_select')
    active = st.session_state.get(key_prefix+'_runner')
    needs_restore = active is None or active.trace_id != selected['plan_id'] or preferred
    if st.button('更新并恢复计划进度', key=key_prefix+'_restore') or needs_restore:
        if flow.request and flow.blocked and flow.request['trace_id'] != selected['plan_id']:
            st.warning('请先处理当前原操作，再恢复其他计划。')
            return
        try:
            active = BankPlanRunner.restore(flow, journal, selected['plan_id'])
            st.session_state[key_prefix+'_runner'] = active
        except (ValueError, ServiceError, ContractError, NeedsInput):
            st.warning('计划暂不可恢复，请保留编号核实；不会另建计划或重新付款。')
            return
    try:
        active._sync()
    except (NeedsInput, ServiceError, ContractError):
        st.warning('计划已有新版本或原操作尚待核实，请更新并恢复进度。')
        return
    st.caption('计划编号：'+active.trace_id)
    labels = {'pending':'未开始','awaiting_bank':'待本人处理或核实','completed':'已完成','stopped':'已停止'}
    ui.render_records([{'步骤':t['title'], '处理时间':beijing(t.get('not_before')),
        '先完成': '、'.join(next(x['title'] for x in active.tasks if x['id']==key) for key in t['depends_on']) or '无',
        '状态':labels[t['status']]} for t in active.tasks], title_field='步骤',
        meta_fields=('先完成', '处理时间', '状态'), limit=None,
        layout='steps', tone='ai', hide_index=True)
    st.caption('已保存计划只安排待办；预留、下单与释放仍分别需要确认或身份验证。')
    if active.paused:
        st.warning('计划已暂停，已提交的原操作仍须核实；暂停不撤销付款或释放预算。')
        stopped=any(t['status']=='stopped' for t in active.tasks)
        if stopped:
            st.caption('原步骤已取消或未完成，请核实回执并按实际情况手动处理；继续不会重建已结束的授权。')
        if not stopped and not active.integrity_blocked and st.button('继续原计划', key=key_prefix+'_resume'):
            active.paused=False
            try:
                active.checkpoint('control')
                st.rerun()
            except NeedsInput as exc:
                st.warning(str(exc))
    elif any(t['status'] not in {'completed','stopped'} for t in active.tasks):
        if st.button('暂停后续准备', key=key_prefix+'_pause'):
            active.paused=True
            try:
                active.checkpoint('control')
                st.rerun()
            except NeedsInput as exc:
                st.warning(str(exc))
        if not flow.blocked and st.button('检查并准备到期下一步', key=key_prefix+'_next'):
            try:
                outcome = active.advance(allow_write=True)
                if outcome is None:
                    st.info('尚未到处理时间，或需先完成前一步；没有新发起付款。')
                else:
                    st.rerun()
            except (NeedsInput, ValueError, ServiceError, ContractError) as exc:
                st.warning(str(exc) if isinstance(exc, (NeedsInput, ValueError)) else '请核实原操作后再继续。')
    if active.receipts:
        st.write('各步骤实际回执')
        for key, receipt in active.receipts.items():
            task = next(t for t in active.tasks if t['id']==key)
            st.text(task['title']+'：'+STATES.get(receipt['status'],receipt['status']))
            with st.expander(task['title']+' · 回执详情'):
                from app.presentation import failure_note
                note=failure_note(receipt)
                if note:st.caption(note)
                st.json(receipt.get('data',receipt.get('error',{})))
