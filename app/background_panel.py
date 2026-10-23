"""Background status and durable notifications; rendering never starts a worker."""
import streamlit as st


def render_background(flow):
    with st.expander('后台任务与通知'):
        with flow.gateway.store.connection() as c:
            health=c.execute("SELECT * FROM worker_lease WHERE name='main'").fetchone()
            rows=[dict(r) for r in c.execute('SELECT * FROM notifications WHERE principal_id=? ORDER BY created_at DESC LIMIT 30',(flow.principal_id,))]
        if health:
            live=health['expires']>flow.gateway.clock().timestamp()
            st.caption(('后台运行中' if live else '后台已停止或心跳过期')+' · 最近检查：'+health['last_tick'])
            if health['error']:st.warning(health['error'])
        else:st.caption('后台尚未启动。打开终端运行下方命令；关闭网页不会停止该进程。')
        st.code('.\\.venv\\Scripts\\python.exe -m bank.worker',language='powershell')
        for row in rows:st.text(('已读 · ' if row['is_read'] else '未读 · ')+row['message'])
        if rows and st.button('将当前通知标为已读',key='inbox_read'):
            with flow.gateway.store.connection(write=True) as c:
                c.executemany('UPDATE notifications SET is_read=1 WHERE notification_key=? AND principal_id=?',[(r['notification_key'],flow.principal_id) for r in rows])
            st.rerun()
