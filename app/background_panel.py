"""Background status and controls; rendering never starts a worker by itself."""
import streamlit as st

from bank.background import BackgroundController


def _render_notifications(flow):
    with flow.gateway.store.connection() as c:
        rows = [dict(r) for r in c.execute(
            'SELECT * FROM notifications WHERE principal_id=? ORDER BY created_at DESC LIMIT 30',
            (flow.principal_id,))]
    for row in rows:
        st.text(('已读 · ' if row['is_read'] else '未读 · ') + row['message'])
    if rows and st.button('将当前通知标为已读', key='inbox_read'):
        with flow.gateway.store.connection(write=True) as c:
            c.executemany('UPDATE notifications SET is_read=1 WHERE notification_key=? AND principal_id=?',
                          [(r['notification_key'], flow.principal_id) for r in rows])
        st.rerun()


def render_background(flow):
    with st.expander('后台任务与通知'):
        if flow.gateway.demo_mode:
            st.caption('当前是明确标识的演示验证模式。创建本机账号并绑定手机后，可在程序内启用本机后台检查；演示模式不会启动后台。')
            _render_notifications(flow)
            return
        controller = BackgroundController(flow.gateway.store)
        try:
            status = controller.status()
        except Exception:
            st.error('后台状态暂不可读取；不会自动启动或停止任何进程。')
            _render_notifications(flow)
            return
        if status.get('starting'):
            st.info('后台正在启动，等待首次检查；尚未确认运行。')
        elif status['running']:
            st.success('后台检查运行中')
            st.caption(f"最近检查：{status['last_tick'] or '启动中'} · 关闭网页不会停止本应用后台。")
        elif status['stopping']:
            st.warning(status['message'])
        elif status['preference']:
            st.error(status['message'])
        else:
            st.caption('后台检查未启用。过期计划不会自动准备待办，但余额、流水、预留和计划仍保存在本机。')
        if status.get('last_error'):
            st.warning('后台最近错误：' + str(status['last_error']))
        if st.button('刷新后台状态', key='bg_refresh'):
            st.rerun()
        if status['running'] or status['stopping'] or status.get('starting'):
            if st.button('停止后台检查', key='bg_stop', width='stretch'):
                result = controller.request_stop(wait=6)
                st.session_state['bg_last_stop'] = result
                st.rerun()
        else:
            label = '重新启用后台检查' if status['preference'] else '启用后台检查'
            if st.button(label, key='bg_start', type='primary', width='stretch'):
                result = controller.ensure_started(enable=True)
                if result.get('started'):
                    st.info('已提交后台启动请求，请刷新状态核实首次检查。')
                    st.rerun()
                else:
                    st.error(result.get('message', '后台启动未完成。'))
        last_stop = st.session_state.get('bg_last_stop')
        if last_stop and last_stop.get('stopping'):
            st.warning('已请求停止。当前检查完成后会退出，不再领取新任务；已开始的原操作需在页面核实。')
        st.caption('停止只影响本应用后台，不会停止其他程序；也不会取消已登记计划或已开始的业务。')
        _render_notifications(flow)
