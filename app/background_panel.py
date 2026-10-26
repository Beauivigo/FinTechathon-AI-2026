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
            st.caption('未绑定手机时不会启动后台。登录并绑定后可在本程序内启用。')
            _render_notifications(flow)
            return
        controller = BackgroundController(flow.gateway.store)
        try:
            status = controller.status()
        except Exception:
            st.error('后台状态暂不可读取；不会自动启动或停止任何进程。')
            _render_notifications(flow)
            return
        if status.get('stop_state') == 'unverified':
            st.warning(status['message'])
        elif status.get('starting'):
            st.info('后台正在启动，等待首次检查；尚未确认运行。')
        elif status['running']:
            st.success('后台检查运行中')
            st.caption(f"最近检查：{status['last_tick'] or '启动中'} · 关闭网页不会停止本应用后台。")
        elif status['stopping']:
            st.warning(status['message'])
        elif status.get('stop_state') == 'stopped':
            st.info(status['message'])
        elif status['preference']:
            st.error(status['message'])
        else:
            st.caption('后台未启用：到期事项不会自动准备，已有余额、流水、预留和计划保留。')
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
        if last_stop:
            if last_stop.get('stop_state') == 'unverified':
                st.warning('停止请求已记录，但无法核实此前后台实例是否已停止；它不会再领取新任务，已发出的操作请保留编号在页面核实。')
            elif last_stop.get('stop_state') == 'stopping':
                st.warning('已请求停止。当前检查完成后会退出，不再领取新任务；已开始的原操作需在页面核实。')
            elif last_stop.get('confirmed_stopped'):
                st.info('后台已确认停止；已登记计划不会取消，重新启用后才准备到期待办。')
        st.caption('停止只影响本应用后台，不会停止其他程序；也不会取消已登记计划或已开始的业务。')
        _render_notifications(flow)
