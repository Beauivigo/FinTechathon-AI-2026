from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.workflow import current, render, perform, LABELS
from card import TARGETS

st.set_page_config(page_title='模拟卡片管理', layout='wide')
st.title('卡片管理')
st.info('工程模拟卡：尚未替换为 E 的正式资料。四种状态操作均需要模拟强验证。')
flow = current()
cards = flow.resources['cards']
st.dataframe(cards, hide_index=True)
if cards:
    selected = st.selectbox('选择卡片', cards, key='card_select',
        format_func=lambda c: f"{c['card_id']} · {c['card_state']}")
    action = st.selectbox('卡片操作', list(TARGETS), format_func=lambda a: LABELS[a], key='card_action')
    st.write(f"{selected['card_state']} → {TARGETS[action]}")
    st.caption('解挂后仍冻结；解冻需另行验证。限额只作样例展示，本页不修改限额。')
    if st.button('提交卡片操作', key='card_submit', disabled=flow.blocked):
        perform(flow.start_action, action, dict(card_id=selected['card_id']))
render(flow)
