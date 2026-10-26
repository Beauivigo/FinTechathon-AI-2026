"""Sidebar exit control: shows unfinished work before a normal application exit."""
import os

import streamlit as st

from bank.lifecycle import exit_summary, normal_exit


def _render_pending(preview):
    operations = preview.get('pending_operations', [])
    plans = preview.get('pending_plans', [])
    if not operations and not plans:
        st.success('没有未完成的银行操作或计划；退出不会删除任何本机数据。')
    else:
        st.warning('以下待办会保留在本机，重新打开后可以继续处理；退出不会自动付款、取消或重置。')
        for row in operations[:8]:
            st.text(f"操作：{row['action']} · {row['status']} · {row['idempotency_key']}")
        for row in plans[:5]:
            st.text(f"计划：{row['plan_id']} · 未完成 {row['unfinished']} 步")
    if preview.get('unread_notifications'):
        st.caption(f"未读站内通知：{preview['unread_notifications']} 条")


def render_exit(store, principal):
    with st.sidebar.expander('退出应用（不是关闭网页）'):
        st.caption('关闭浏览器标签页只会离开页面，本机后台仍可继续；退出应用会停止本机页面服务，并请求本应用后台停止。')
        if st.button('查看未完成待办并准备退出', key='exit_prepare'):
            try:
                st.session_state['exit_preview'] = exit_summary(store, principal)
            except Exception:
                st.error('暂时无法读取待办；未执行退出。')
        preview = st.session_state.get('exit_preview')
        if preview:
            _render_pending(preview)
            background = preview.get('background') or {}
            if background.get('running'):
                st.caption('后台正在运行；确认退出时会先请求后台停止。')
            if background.get('last_error'):
                st.caption('后台最近错误：' + str(background['last_error']))
            if st.button('确认退出应用并停止本机服务', key='exit_confirm', type='primary'):
                try:
                    result = normal_exit(store, principal)
                except Exception:
                    st.error('退出准备未完成；本应用继续运行，数据未被删除。')
                    return
                if not result.get('app_exit_allowed'):
                    st.session_state['exit_preview'] = result
                    st.error('本应用后台尚未确认停止；为避免留下本应用服务，暂不退出。请稍后再次确认。')
                    return
                print('应用正常退出：未完成待办与计划已保留在本机。')
                os._exit(0)
            if st.button('取消退出准备', key='exit_cancel'):
                st.session_state.pop('exit_preview', None)
                st.rerun()
