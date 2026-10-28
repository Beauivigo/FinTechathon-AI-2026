"""User settings and advanced tools, separated from normal business tasks."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app import ui, visual
from app.auth_ui import gate, service
from app.workflow import current
from app.background_panel import render_background
from app.model_settings import render_settings

st.set_page_config(page_title='我的', layout='wide')
# 先注入固定静态样式，再渲染页面内容；st.set_page_config 已在本文件最前面调用。
visual.apply('core')
gate()
ui.render_sidebar_nav('mine')
with visual.band('core', 'mine'):
    ui.render_page_header('我的')
    ui.render_app_nav('mine')
status=service().account_status()
with visual.card('core', 'security'):
    st.subheader('登录与安全')
    if status['initialized']:
        st.write('当前账号：'+status['username'])
        st.caption('手机验证器已启用；登录不等于批准交易。侧栏可退出登录；手机丢失时请在登录页使用恢复码重新绑定。')
    else:
        st.write('体验示例账户')
        st.caption('尚未设置本机登录。设置后管理同一账本，不新增余额或清空流水。')
        if st.button('设置本机登录与手机验证',key='mine_setup'):
            st.session_state['demo_experience']=False
            st.session_state['setup_requested']=True
            st.rerun()
ui.render_service_link('cards','银行卡管理',widget_key='mine_cards')
flow=current()
render_background(flow)
with visual.card('core', 'settings'):
    st.subheader('高级设置')
    render_settings()
with st.expander('帮助与示例资料'):
    st.caption('本地模拟项目，不连接真实资金。已有操作以账本为准；关闭网页不会取消预约或已受理业务。')
    ui.render_service_link('accounts','历史样例分析（独立资料账本）',widget_key='mine_history')
    ui.render_service_link('home','使用指南与当前账户',widget_key='mine_guide')
