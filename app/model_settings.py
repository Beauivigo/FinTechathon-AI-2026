"""本机模型设置：显式切换来源，密码框更新，保存时使用Windows加密。"""
import streamlit as st
from agent.model_credentials import CredentialStore, CredentialError
from agent.model_providers import PROVIDERS


def _provider_changed():
    provider = st.session_state['bank_model_provider']
    if provider not in PROVIDERS:
        return
    st.session_state['bank_model_choice'] = PROVIDERS[provider].models[0]
    st.session_state['bank_model_custom'] = ''
    st.session_state['bank_model_new_key'] = ''
    st.session_state.pop('bank_model_settings_message', None)


def _save(mode, *, clear=False):
    try:
        raw = st.session_state.get('bank_model_new_key', '')
        key = raw.strip() if mode == 'independent' and raw.strip() else None
        provider = st.session_state.get('bank_model_provider', 'deepseek')
        model = st.session_state.get('bank_model_custom', '').strip() or st.session_state.get('bank_model_choice', 'deepseek-flash')
        if mode == 'desktop':
            provider, model = 'deepseek', 'deepseek-flash'
        CredentialStore().save(mode=mode, provider=provider, model=model, key=key, clear=clear)
        if mode == 'desktop':
            st.session_state['bank_model_provider'] = provider
            _provider_changed()
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
        st.caption('本机保存来源：' + sources[profile['mode']] + ' · ' + PROVIDERS[profile['provider']].label)
        st.caption('服务器环境变量如有配置会优先使用；设置仅影响模型调用，不修改银行操作和授权。')
        st.session_state.setdefault('bank_model_provider', profile['provider'])
        provider = st.selectbox('模型服务商', list(PROVIDERS), format_func=lambda name: PROVIDERS[name].label,
            key='bank_model_provider', on_change=_provider_changed)
        if provider not in PROVIDERS:
            st.error('请选择已支持的模型服务商。')
            return
        options = list(PROVIDERS[provider].models)
        saved_model = profile['model'] if profile['provider'] == provider else options[0]
        if saved_model not in options:
            options.append(saved_model)
        st.session_state.setdefault('bank_model_choice', saved_model)
        if st.session_state['bank_model_choice'] not in options:
            st.session_state['bank_model_choice'] = saved_model
        st.selectbox('独立密钥使用的模型', options, key='bank_model_choice')
        st.text_input('其他模型 ID（选填，填写后优先使用）', key='bank_model_custom',
            help='填写服务商已开通且支持Chat Completions与JSON输出的模型ID；豆包也可填写推理接入点ep-编号。')
        st.caption('官方接口：' + PROVIDERS[provider].base_url)
        st.caption('切换服务商需输入对应密钥，原密钥不会自动复用；当前仅保存一份独立配置。')
        st.text_input('新的银行项目API密钥', type='password', key='bank_model_new_key', autocomplete='off')
        st.button('保存或修改独立密钥', key='bank_model_save', on_click=_save, args=('independent',))
        st.button('沿用桌宠本机配置', key='bank_model_desktop', on_click=_save, args=('desktop',), disabled=provider != 'deepseek')
        st.button('清除独立密钥并停用本机配置', key='bank_model_key_clear', on_click=_save, args=('missing',), kwargs={'clear':True})
        st.caption('独立密钥按Windows当前用户加密保存，页面不会回显已保存密钥。切换来源不重发旧请求。')
        if st.session_state.get('bank_model_settings_message'):
            st.info(st.session_state['bank_model_settings_message'])
