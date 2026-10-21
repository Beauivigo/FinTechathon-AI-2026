"""本机模型设置：显式切换来源，密码框更新，保存时使用Windows加密。"""
import streamlit as st
from agent.model_credentials import CredentialStore, CredentialError


def _save(mode, *, clear=False):
    try:
        raw = st.session_state.get('bank_model_new_key', '')
        key = raw.strip() if mode == 'independent' and raw.strip() else None
        CredentialStore().save(mode=mode, model=st.session_state.get('bank_model_choice', 'deepseek-flash'), key=key, clear=clear)
        st.session_state['bank_model_settings_message'] = '模型设置已保存；后续新请求使用新配置。'
        st.session_state['bank_model_new_key'] = ''
    except CredentialError as exc:
        st.session_state['bank_model_settings_message'] = str(exc)


def render_settings():
    with st.expander('模型设置'):
        try:
            profile = CredentialStore().read()
        except CredentialError as exc:
            st.warning(str(exc))
            return
        sources = {'missing':'未配置', 'desktop':'沿用桌宠本机配置', 'independent':'银行项目独立密钥'}
        st.caption('本机保存来源：' + sources[profile['mode']])
        st.caption('服务器环境变量如有配置会优先使用；设置仅影响模型调用，不修改银行操作和授权。')
        st.session_state.setdefault('bank_model_choice', profile['model'])
        st.selectbox('独立密钥使用的模型', ['deepseek-flash','deepseek-v4-pro'], key='bank_model_choice')
        st.text_input('新的银行项目API密钥', type='password', key='bank_model_new_key', autocomplete='off')
        st.button('保存或修改独立密钥', key='bank_model_save', on_click=_save, args=('independent',))
        st.button('沿用桌宠本机配置', key='bank_model_desktop', on_click=_save, args=('desktop',))
        st.button('清除独立密钥并停用本机配置', key='bank_model_key_clear', on_click=_save, args=('missing',), kwargs={'clear':True})
        st.caption('独立密钥按Windows当前用户加密保存，页面不会回显已保存密钥。切换来源不重发旧请求。')
        if st.session_state.get('bank_model_settings_message'):
            st.info(st.session_state['bank_model_settings_message'])
