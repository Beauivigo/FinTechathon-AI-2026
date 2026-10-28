"""实际模型驱动的自然语言意图、任务计划和银行网关执行。"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app import ui, visual
from app.model_settings import render_settings
from app.model_bank_panel import render_model_bank
from shared import ServiceError, ContractError

st.set_page_config(page_title='AI银行助手',layout='wide')
# 先注入固定静态样式，再渲染页面内容；st.set_page_config 已在本文件最前面调用。
visual.apply('ai')
from app.auth_ui import gate
gate()
ui.render_sidebar_nav('assistant')
with visual.band('ai', 'assistant'):
    ui.render_page_header('AI银行助手','理解需求、补充信息、规划步骤，并按银行权限完成。')
    ui.render_app_nav('assistant')
from app.model_settings import render_configuration_status
render_configuration_status()
try:
    render_model_bank()
except (ServiceError,ContractError):
    st.error('银行数据暂不可核实，请先查询原操作，勿重复提交。')
