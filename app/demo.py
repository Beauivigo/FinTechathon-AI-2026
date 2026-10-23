"""页面共享的可信本地模拟身份和网关入口，不接受用户提供身份或数据库路径。"""
from bank.gateway import BankGateway
from bank.seed import initialize, demo_path
from bank.store import Store

PRINCIPAL = 'demo_user_a'


def gateway():
    store = Store(demo_path())
    initialize(store)
    import streamlit as st
    return BankGateway(store,session_token=st.session_state.get('auth_token'))
