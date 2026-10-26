"""Task-based teaching UI, isolated from every bank and model operation."""
from dataclasses import dataclass, replace
import streamlit as st

STATE_KEY = '_bank_guide_v1'
TOPICS = {
    'account': ('了解账户', (
        ('先看可用余额', '首页“账户概览”显示当前账户可使用的余额。它与预留金额分别展示。'),
        ('认识预留金额', '预留金额是已为其他事项留出的部分，不应当作可以再次使用的钱。'),
        ('找到待办与记录', '首页下方显示待处理事项和近期交易。结果待核实的业务，应到对应页面查询原操作。'),
    ), 'home', '回到账户首页'),
    'transfer': ('练习转账', (
        ('找到转账入口', '首页“转账”或侧栏“转账与收款”都可以进入转账汇款。这里先用教学数据练习。'),
        ('准备收款人和金额', '试着调整下面的练习信息。实际办理时，收款人重名会要求进一步选择账户。'),
        ('核对后再确认', '检查收款人、账户与金额。实际办理中的确认或身份验证，都会在原业务页面单独进行。'),
        ('去哪里查看结果', '实际转账完成后，在“账单流水”查入账记录，在“操作记录”恢复待办；结果不确定时先查询原操作。'),
    ), 'transfer', '去转账页面'),
    'bills': ('查看账单', (
        ('打开当前账户流水', '首页“账单与流水”展示当前账户的账本。“资料账单”是独立的已核验示例资料。'),
        ('选择统计期间', '在账单分析中选择账户和月份、年份或日期范围，再点击“查询账单”。'),
        ('读懂统计结果', '毛支出与退款分别核对。提示只是进一步核实的线索；没有提示，也不能证明没有异常。'),
    ), 'bills', '去账单页面'),
}


@dataclass(frozen=True)
class GuideState:
    opened: bool = False
    dismissed: bool = False
    topic: str | None = None
    step: int = 0
    completed: bool = False
    practice_payee: str = '李明'
    practice_amount: float = 200.0


def transition(state: GuideState, event: str, topic: str | None = None) -> GuideState:
    """Change teaching state only; never accept a business action as an event."""
    if event == 'open':
        return replace(state, opened=True, dismissed=True, topic=None, step=0, completed=False)
    if event == 'close':
        return replace(state, opened=False, dismissed=True)
    if event == 'start' and topic in TOPICS:
        return GuideState(True, True, topic)
    if state.topic not in TOPICS:
        return state
    if event == 'back' and not state.completed:
        return replace(state, step=max(0, state.step - 1))
    if event == 'next' and not state.completed:
        last = len(TOPICS[state.topic][1]) - 1
        return replace(state, step=min(last, state.step + 1), completed=state.step == last)
    if event == 'replay':
        return replace(state, opened=True, step=0, completed=False)
    return state


def _change(state, event, topic=None):
    if state.topic == 'transfer' and state.step == 1:
        # Streamlit removes absent widgets; keep practice values for Back.
        state = replace(state,
                        practice_payee=st.session_state.get('bank_guide_payee', state.practice_payee),
                        practice_amount=st.session_state.get('bank_guide_amount', state.practice_amount))
    st.session_state[STATE_KEY] = transition(state, event, topic)
    st.rerun()


def render_guide() -> str | None:
    """Return an explicit page-selection action, never a bank request."""
    state = st.session_state.get(STATE_KEY)
    if not isinstance(state, GuideState):
        state = GuideState()
        st.session_state[STATE_KEY] = state
    if not state.opened:
        if not state.dismissed:
            with st.container(gap='xsmall'):
                st.write('**第一次使用？** 用几步了解账户、转账和账单。')
                with st.container(horizontal=True, gap='small'):
                    if st.button('开始使用指南', key='bank_guide_open', icon=':material/explore:'):
                        _change(state, 'open')
                    if st.button('暂时跳过', key='bank_guide_skip', type='tertiary'):
                        _change(state, 'close')
        elif st.button('使用指南', key='bank_guide_open', type='tertiary', icon=':material/help:'):
            _change(state, 'open')
        return None

    with st.container(border=True, gap='small'):
        if state.topic is None:
            st.header('你想先了解什么？')
            st.caption('可以随时关闭，之后从首页重新打开。')
            with st.container(horizontal=True, gap='small'):
                for key, (label, _steps, _action, _destination) in TOPICS.items():
                    if st.button(label, key='bank_guide_topic_' + key, width='stretch'):
                        _change(state, 'start', key)
        else:
            label, steps, action, destination = TOPICS[state.topic]
            st.header(label, icon=':material/school:')
            if state.completed:
                st.write('**本次学习已完成。** 练习没有创建银行操作，也没有改变账户。')
                with st.container(horizontal=True, gap='small'):
                    if st.button(destination, key='bank_guide_destination', type='primary'):
                        st.session_state[STATE_KEY] = transition(state, 'close')
                        return action
                    if st.button('再学一遍', key='bank_guide_replay'):
                        _change(state, 'replay')
                    if st.button('学习其他任务', key='bank_guide_choose'):
                        _change(state, 'open')
            else:
                heading, description = steps[state.step]
                st.progress((state.step + 1) / len(steps), text=f'第 {state.step + 1} / {len(steps)} 步 · {heading}')
                st.write(description)
                if state.topic == 'transfer' and state.step == 1:
                    st.caption('教学示例，不会写入账本或发起转账。')
                    left, right = st.columns(2)
                    with left:
                        st.text_input('练习收款人', value=state.practice_payee, key='bank_guide_payee', max_chars=30)
                    with right:
                        st.number_input('练习金额（元）', min_value=0.01, value=state.practice_amount,
                                        step=1.0, format='%.2f', key='bank_guide_amount')
                if state.topic == 'transfer' and state.step == 2:
                    st.caption('以下是教学数据，尚未生成银行请求。')
                    st.text('收款人：' + state.practice_payee)
                    st.text('练习金额：¥ %.2f' % state.practice_amount)
                    st.caption('实际办理时还需核对完整的收款账户；下一步只继续教学。')
                with st.container(horizontal=True, gap='small'):
                    if st.button('上一步', key='bank_guide_back', disabled=state.step == 0):
                        _change(state, 'back')
                    if st.button('完成学习' if state.step == len(steps) - 1 else '下一步',
                                 key='bank_guide_next', type='primary'):
                        _change(state, 'next')
                    if st.button('换个任务', key='bank_guide_choose', type='tertiary'):
                        _change(state, 'open')
        if st.button('关闭指南', key='bank_guide_close', type='tertiary', icon=':material/close:'):
            _change(state, 'close')
    return None
