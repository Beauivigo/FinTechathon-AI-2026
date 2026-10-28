"""Local identity gate shared by every page.

First-run setup, login, phone binding, recovery and normal exit all happen in
the app.  Secrets stay in this device's memory/SQLite sealing; they are never
rendered in the URL, sent to a model, written to logs or placed in chat.
"""
import io

import streamlit as st

from bank.auth import AuthService, SETUP_ACTIVE, SETUP_BINDING, SETUP_RECOVERY
from bank.first_run import setup_allowed
from bank.store import Store
from bank.seed import demo_path, initialize
from shared import ServiceError


FLOW_KEYS = ('conversation', 'action_flow', 'model_bank_flow', 'model_caller',
             'model_dialogue', 'model_runner', 'model_runner_version')


def service():
    store = Store(demo_path())
    initialize(store)
    return AuthService(store)


def clear_flows():
    for key in (*FLOW_KEYS, 'bg_restore_done', 'exit_preview', 'birthday_progress_runner',
                'birthday_reviewed', 'birthday_open_plan', 'pending_resume', 'direct_edit_seed',
                'subscription_snapshot', 'reminder_snapshot'):
        st.session_state.pop(key, None)


def error_text(exc):
    """User-facing text for the auth/authorization errors we intentionally expose."""
    if isinstance(exc, ServiceError) and exc.code in {
            'AUTH_INVALID', 'AUTH_EXPIRED', 'SAFETY_LOCKED', 'INVALID_STATE', 'INVALID_REQUEST'}:
        return exc.message
    return '调用未完成，请查询原操作；不要重复发起或重置来绕过核查。'


def _flash(message, level='info'):
    st.session_state['auth_flash'] = (level, message)


def _show_flash():
    item = st.session_state.pop('auth_flash', None)
    if not item:
        return
    level, message = item
    {'success': st.success, 'warning': st.warning, 'error': st.error}.get(level, st.info)(message)


def _setup_allowed():
    try:
        headers = dict(st.context.headers)
        listener = st.get_option('server.address')
    except Exception:
        return False
    return setup_allowed(headers, server_address=listener)


def reattach_flows(auth, token):
    """Keep the original business draft/results across re-login; only rebind the token."""
    for key in FLOW_KEYS:
        flow = st.session_state.get(key)
        if flow is not None and hasattr(flow, 'gateway'):
            try:
                flow.gateway.session_token = token
            except Exception:
                pass


def _maybe_restore_background(auth):
    if st.session_state.get('bg_restore_done'):
        return
    st.session_state['bg_restore_done'] = True
    try:
        from bank.background import BackgroundController
        controller = BackgroundController(auth.store)
        if controller.preference():
            controller.ensure_started(enable=False)
    except Exception:
        # The background panel reports the concrete persisted error; login is not blocked.
        pass


def _login_form(auth, *, pending_setup=False):
    if pending_setup:
        st.caption('账号已经创建，不会重复建号。输入刚才设置的密码继续；刷新或关闭页面不会丢失进度。')
    with st.form('auth_login'):
        username = st.text_input('用户名', max_chars=64)
        password = st.text_input('登录密码', type='password', max_chars=256)
        submitted = st.form_submit_button('登录')
    if submitted:
        token, error, retry = auth.login_detail(username, password)
        if token:
            st.session_state['auth_token'] = token
            st.session_state['auth_had_session'] = False
            reattach_flows(auth, token)
            _flash('登录成功。登录本身不批准任何付款。', 'success')
            st.rerun()
        elif error == 'LOCKED':
            st.warning(f"连续登录失败，已临时锁定；约 {max(1, retry // 60)} 分钟后可再试。")
        else:
            st.warning('用户名或密码不正确。')


def _render_recovery_form(auth):
    with st.expander('丢失手机：使用密码和未使用恢复码重新绑定'):
        st.caption('恢复只撤销旧手机绑定与旧会话，允许重新扫码；不会批准、重发或自动执行任何付款。')
        with st.form('auth_recover'):
            name = st.text_input('恢复账号', max_chars=64)
            password = st.text_input('账号密码', type='password', max_chars=256)
            recovery = st.text_input('一枚未使用的恢复码', type='password', max_chars=128)
            submitted = st.form_submit_button('撤销原会话并重新绑定')
        if submitted:
            try:
                auth.recover(name, password, recovery)
                st.session_state.pop('auth_token', None)
                st.session_state['auth_had_session'] = False
                clear_flows()
                _flash('旧手机绑定和相关登录会话已撤销。请用新手机登录、扫码并输入动态码；恢复本身没有批准任何付款。', 'success')
                st.rerun()
            except ServiceError as exc:
                st.warning(error_text(exc))


def _render_binding(auth, token, *, heading='登录并绑定手机验证器'):
    st.title(heading)
    st.info('用手机验证器扫码并输入动态码。二维码、密钥和动态码不要发到聊天。')
    try:
        import qrcode
        uri = auth.enrollment(token)
        image = qrcode.make(uri)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        st.image(buffer.getvalue(), width=240, alt='手机验证器绑定二维码，仅在本机查看')
        with st.form('auth_enroll'):
            code = st.text_input('手机当前动态码', type='password', max_chars=6)
            submitted = st.form_submit_button('完成绑定')
        if submitted:
            try:
                auth.finish_enrollment(token, code)
                _flash('手机验证已启用。今后红色业务仍需当次动态码，登录不等于批准付款。', 'success')
                st.rerun()
            except ServiceError as exc:
                st.warning(error_text(exc))
    except Exception:
        st.warning('本机加密或绑定暂不可用，请检查安装；不会退回演示验证，也不会批准付款。')


def _entry_shell():
    """Scope the standalone identity screens without affecting bank navigation."""
    return st.container(key='vx-auth-entry', border=False)


def _render_first_run(auth):
    if st.session_state.get('setup_requested'):
        with _entry_shell():
            st.title('设置本机账号')
            st.caption('本机账号用于管理现有示例账本，不是开立真实银行账户；不会新增余额、清空流水或另建资金事实。')
            if not _setup_allowed():
                st.warning('首次设置只允许从本机回环地址（127.0.0.1/localhost）打开。当前访问地址不符合条件，没有创建入口，现有数据也未修改。')
                if st.button('返回欢迎页', key='setup_cancel'):
                    st.session_state['setup_requested'] = False
                    st.rerun()
                return
            with st.expander('设置用户名和密码', expanded=True):
                with st.form('setup_begin'):
                    username = st.text_input('设置用户名', max_chars=64)
                    password = st.text_input('设置密码（至少12字符）', type='password', max_chars=256)
                    confirm = st.text_input('再次输入密码', type='password', max_chars=256)
                    submitted = st.form_submit_button('下一步：创建账号并生成恢复码')
                if submitted:
                    try:
                        result = auth.begin_setup(username, password, confirm)
                        st.session_state['auth_token'] = result['token']
                        st.session_state['setup_requested'] = False
                        clear_flows()
                        _flash('本机账号已创建。下一步保存一次性恢复码，再绑定手机；刷新不会重复建号。', 'success')
                        st.rerun()
                    except ServiceError as exc:
                        st.error(error_text(exc))
            st.caption('后续步骤：保存恢复码 → 绑定手机验证器 → 进入银行。')
            if st.button('返回欢迎页', key='setup_cancel'):
                st.session_state['setup_requested'] = False
                st.rerun()
            return
    if st.session_state.get('demo_experience'):
        st.sidebar.caption('体验示例账户 · 本机示例账本')
        if st.sidebar.button('设置本机账号', key='demo_to_setup'):
            st.session_state['demo_experience'] = False
            st.session_state['setup_requested'] = True
            st.rerun()
        return
    with _entry_shell():
        st.title('欢迎使用银行助手')
        st.caption('本地模拟项目，不连接真实资金。请选择体验现有示例账户，或设置本机账号来管理同一套示例账本。')
        left, right = st.columns(2, gap='large')
        with left, st.container(border=True, key='vx-motion-welcome-demo'):
            st.subheader('体验示例账户')
            st.write('先使用现有示例账本和日常账户熟悉流程；不会新增真实资金。')
            if st.button('体验示例账户', key='welcome_demo', type='primary', width='stretch'):
                st.session_state['demo_experience'] = True
                st.session_state['setup_requested'] = False
                st.rerun()
        with right, st.container(border=True, key='vx-motion-welcome-setup'):
            st.subheader('设置本机账号')
            st.write('设置用户名和密码、保存恢复码并绑定手机验证器。')
            if st.button('设置本机账号', key='welcome_setup', width='stretch'):
                st.session_state['demo_experience'] = False
                st.session_state['setup_requested'] = True
                st.rerun()
        st.info('设置账号不会另开一套资金，也不代表真实银行开户。')


def _resume_setup(auth, status, token):
    st.title('继续完成首次设置')
    st.caption('账号已创建，不会重复建号；在恢复码确认和手机绑定完成前不会进入真实业务。')
    principal = None
    if token:
        try:
            auth.principal(token, require_bound=False)
            principal = True
        except ServiceError:
            principal = None
    if not principal:
        if token:
            st.info('登录已过期或已失效。请重新输入密码继续，原设置进度仍在。')
        _login_form(auth, pending_setup=True)
        return
    state = auth.account_status()['setup_state']
    if state == SETUP_RECOVERY:
        try:
            codes = auth.pending_recovery(token)
        except ServiceError as exc:
            st.error(error_text(exc))
            st.caption('未放宽身份校验，也没有重新生成恢复码。')
            return
        st.subheader('请离线保存一次性恢复码')
        st.warning('不要截图发到聊天、不要上传网盘或发给AI。关闭本页前请抄写到安全位置。')
        st.code('\n'.join(codes), language=None)
        st.caption('每枚恢复码只能使用一次。确认保存前刷新页面，仍可用密码继续查看同一组恢复码；不会重复发放额外的可用恢复码。')
        with st.form('setup_saved'):
            saved = st.checkbox('我已将以上恢复码离线保存到安全位置')
            submitted = st.form_submit_button('我已保存，继续绑定手机')
        if submitted:
            if not saved:
                st.warning('请先确认已经安全保存恢复码。')
            else:
                try:
                    auth.confirm_recovery_saved(token)
                    _flash('恢复码已确认保存，下一步绑定手机验证器。', 'success')
                    st.rerun()
                except ServiceError as exc:
                    st.error(error_text(exc))
        return
    if state == SETUP_BINDING:
        _render_binding(auth, token, heading='绑定手机验证器')
        return
    if state == SETUP_ACTIVE and status['bound']:
        return
    _render_binding(auth, token, heading='绑定手机验证器')


def gate():
    auth = service()
    try:
        status = auth.account_status()
    except Exception:
        with _entry_shell():
            st.error('本机身份数据暂不可用。请检查数据库，不要删除数据库来绕过核查。')
            st.stop()
    token = st.session_state.get('auth_token')
    _show_flash()
    if not status['initialized']:
        _render_first_run(auth)
        if st.session_state.get('setup_requested') or not st.session_state.get('demo_experience'):
            st.stop()
        return
    if status['setup_state'] in (SETUP_RECOVERY, SETUP_BINDING):
        with _entry_shell():
            _resume_setup(auth, status, token)
            st.stop()
            return
    principal = None
    if token:
        if auth.session_status(token) != 'active':
            st.session_state['auth_had_session'] = True
            st.info('登录已过期或已失效。原业务待办保存在本机，重新登录后可恢复；不会自动付款。')
        else:
            try:
                principal = auth.principal(token, require_bound=False)
            except ServiceError:
                principal = None
    if principal:
        if not status['bound']:
            with _entry_shell():
                _render_binding(auth, token, heading='完成手机验证器绑定')
                _render_recovery_form(auth)
                st.stop()
                return
        with st.sidebar:
            st.caption(f"{status['username']} · 手机验证已启用")
            st.caption('本机账号 · 已登录')
            try:
                st.caption(f"一次性恢复码剩余 {auth.remaining_recovery(token)} 枚")
            except ServiceError:
                pass
            if st.button('退出登录', key='auth_logout'):
                auth.logout(token)
                st.session_state.pop('auth_token', None)
                st.session_state['auth_had_session'] = False
                clear_flows()
                st.rerun()
        _maybe_restore_background(auth)
        try:
            from app.lifecycle_panel import render_exit
            render_exit(auth.store, principal)
        except Exception:
            pass
        return
    with _entry_shell():
        st.title('登录本机银行')
        if st.session_state.get('auth_had_session'):
            st.caption('重新登录后，原业务不会自动执行；请在对应页面恢复并重新核对/验证。')
        _login_form(auth)
        _render_recovery_form(auth)
        st.stop()


def transaction_code(flow, *, key):
    if flow.gateway.demo_mode:
        return None
    challenge = flow.result['next_action']['challenge_id']
    value = st.text_input('本次交易的手机动态码', type='password', max_chars=6,
                          key=key + '_' + challenge,
                          help='由手机验证器生成；只用于当前这一笔交易，不会发送给模型。')
    st.caption('动态码输入只在本机页面使用；登录或绑定手机都不等于批准付款。')
    return value
