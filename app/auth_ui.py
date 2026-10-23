"""Local identity gate shared by every page; enrollment secrets stay on this device."""
import io
import streamlit as st
from bank.auth import AuthService
from bank.store import Store
from bank.seed import demo_path,initialize
from shared import ServiceError


def service():
    store=Store(demo_path());initialize(store)
    return AuthService(store)


def clear_flows():
    for key in ('conversation','action_flow','model_bank_flow','model_caller','model_dialogue','model_runner','model_runner_version'):
        st.session_state.pop(key,None)


def gate():
    auth=service()
    with auth.store.connection() as c:
        active=bool(c.execute('SELECT 1 FROM auth_users LIMIT 1').fetchone())
    if not active:
        with st.sidebar.expander('启用真实手机验证'):
            st.caption('当前为模拟验证。首次请在本机终端初始化账号，安全保存恢复码，再刷新并扫码绑定手机。')
            st.code('.\\.venv\\Scripts\\python.exe -m bank.auth',language='powershell')
        return
    token=st.session_state.get('auth_token')
    try:principal=auth.principal(token)
    except ServiceError:principal=None
    if principal:
        with st.sidebar:
            st.caption('已登录 · 手机验证已启用')
            if st.button('退出登录',key='auth_logout'):
                auth.logout(token);st.session_state.pop('auth_token',None);clear_flows();st.rerun()
        return
    st.title('登录并绑定手机验证器')
    try:auth.principal(token,require_bound=False);logged=True
    except ServiceError:logged=False
    if not logged:
        with st.form('auth_login'):
            username=st.text_input('用户名',max_chars=64)
            password=st.text_input('登录密码',type='password',max_chars=256)
            if st.form_submit_button('登录'):
                try:st.session_state.auth_token=auth.login(username,password);clear_flows();st.rerun()
                except ServiceError:st.warning('登录未通过或暂时锁定，请核对后重试。')
    else:
        st.info('请用手机验证器扫码，然后输入手机显示的动态码。不要把二维码、密钥或验证码发到聊天。')
        try:
            import qrcode
            uri=auth.enrollment(token);image=qrcode.make(uri);buffer=io.BytesIO();image.save(buffer,format='PNG')
            st.image(buffer.getvalue(),width=240,alt='手机验证器绑定二维码，仅在本机查看')
            with st.form('auth_enroll'):
                code=st.text_input('手机当前动态码',type='password',max_chars=6)
                if st.form_submit_button('完成绑定'):
                    try:auth.finish_enrollment(token,code);st.rerun()
                    except ServiceError:st.warning('动态码未通过、已使用或暂时锁定。')
        except Exception:
            st.warning('本机加密或绑定暂不可用，请检查安装；不会退回演示验证。')
    with st.expander('丢失手机：使用离线恢复码重新绑定'):
        with st.form('auth_recover'):
            name=st.text_input('恢复账号',max_chars=64)
            password=st.text_input('账号密码',type='password',max_chars=256)
            recovery=st.text_input('一枚未使用的恢复码',type='password',max_chars=128)
            if st.form_submit_button('撤销原会话并重新绑定'):
                try:auth.recover(name,password,recovery);st.session_state.pop('auth_token',None);clear_flows();st.rerun()
                except ServiceError:st.warning('恢复未通过或暂时锁定。')
    st.stop()


def transaction_code(flow,*,key):
    if flow.gateway.demo_mode:return None
    return st.text_input('本次交易的手机动态码',type='password',max_chars=6,key=key+'_'+flow.result['next_action']['challenge_id'])
