"""模型对话展示；只在用户提交时调用会话，不持有银行或密钥。"""


def _assistant_text(raw):
    """Show only the validated reply text, never the raw model JSON envelope."""
    import json
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return '已记录一条未执行的草稿。'
    if isinstance(value, dict):
        reply = value.get('reply')
        if isinstance(reply, str) and reply.strip():
            return reply.strip()
    return '已记录一条未执行的草稿。'


def render_model_dialogue(session, *, blocked=False, allow_back=True,
                          key_prefix='bank_model', bank_progress=None):
    import streamlit as st
    from app import ui
    from app.presentation import (dialogue_controls, dialogue_note, handoff_destinations,
                                  missing_text)
    # Generated button widget keys: <prefix>_send, _pause, _resume, _back,
    # _handoff, _clear; the literal keys are unchanged.
    clear_key = key_prefix + '_input_clear'
    if st.session_state.pop(clear_key, False):
        # Clear before the text area is created: widget state cannot be rewritten
        # after instantiation within the same run.
        st.session_state[key_prefix + '_input'] = ''
    st.subheader('用自然语言办理业务')
    st.caption('模型负责理解需求和规划；结果以账本回执为准，付款及敏感操作仍需确认或验证。')
    phase_before = session.snapshot()['phase']
    note = dialogue_note(phase_before, write_blocked=blocked)
    if note:
        st.info(note)
    text = st.text_area('描述需求或补充上一条信息', key=key_prefix + '_input',
                        max_chars=1000, disabled=phase_before == 'locked')
    controls = dialogue_controls(phase_before, allow_back=allow_back)
    if st.button('发送需求', key=key_prefix + '_send', disabled=not controls['send']):
        with st.spinner('正在理解需求…'):
            session.submit(text)
        snap = session.snapshot()
        if text.strip() and snap['phase'] not in {'error', 'locked'} and not snap.get('error'):
            st.session_state[clear_key] = True
        st.rerun()
    with st.container(horizontal=True):
        if controls['pause'] and st.button('暂停', key=key_prefix + '_pause'):
            session.pause()
            st.rerun()
        if controls['resume'] and st.button('继续草稿', key=key_prefix + '_resume', type='primary'):
            session.resume()
            st.rerun()
        if controls['back'] and st.button('返回修改草稿', key=key_prefix + '_back'):
            session.back()
            st.rerun()
        if controls['handoff'] and st.button('改为手动处理', key=key_prefix + '_handoff'):
            session.handoff()
            st.rerun()
        if controls['clear'] and st.button('新需求', key=key_prefix + '_clear'):
            session.clear()
            st.session_state[clear_key] = True
            st.rerun()
    snap = session.snapshot()
    history = list(snap['history'])
    last_assistant = next((index for index in range(len(history) - 1, -1, -1)
                           if history[index]['role'] == 'assistant'), None)
    for index, message in enumerate(history):
        reply = _assistant_text(message['content']) if message['role'] == 'assistant' else None
        if (index == last_assistant and snap['reply'] and not snap['error']
                and reply == snap['reply']):
            # The current clarification/proposal below already shows this reply.
            continue
        with st.chat_message(message['role']):
            st.text(message['content'] if message['role'] == 'user' else reply)
    if snap['error']:
        st.warning(snap['error'])
    elif snap['phase'] == 'clarify':
        # The model's question is shown as draft help, never as a bank fact.
        if snap['reply']:
            st.info(snap['reply'])
        count = len(snap['missing'] or ())
        if count:
            st.write(f'还需要补充 {count} 项：{missing_text(snap["missing"])}')
        else:
            st.info('需求还有信息需要核对，请补充具体对象和要求。')
    elif snap['phase'] == 'unsupported':
        st.info('当前不支持这项业务，请明确其他需求或改为手动办理。')
    elif snap['phase'] in {'proposal', 'plan', 'handoff'} and snap['reply']:
        st.info(snap['reply'])
    if snap['fields']:
        with st.expander('当前草稿核对信息', expanded=snap['phase'] == 'clarify'):
            from app.presentation import FIELD_LABELS
            for key, value in snap['fields'].items():
                st.text(FIELD_LABELS.get(key, key) + '：' + str(value))
            st.caption('以上均为尚未执行的草稿，不是银行授权或回执。')
    if snap['phase'] == 'handoff':
        intents = [task.get('intent') for task in (snap['tasks'] or ())]
        if snap['intent']:
            intents = [snap['intent']]
        if not intents:
            intents = [None]  # fall back to the home manual entry
        shown = set()
        for intent in intents:
            if len(shown) >= 3:
                break
            for key, label in handoff_destinations(intent):
                if key in shown:
                    continue
                shown.add(key)
                ui.render_service_link(key, label, widget_key=key_prefix + '_manual_' + key)
        st.caption('手动入口不会自动重建或重复执行原计划；已有待办请先核实。')
    if snap['phase'] in {'paused', 'handoff'}:
        st.warning('自动推进已停止；已提交的银行操作不会因暂停或回退而撤销。')
    return snap
