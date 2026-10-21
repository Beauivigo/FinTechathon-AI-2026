"""模型对话展示；只在用户提交时调用会话，不持有银行或密钥。"""


def render_model_dialogue(session,*,blocked=False,allow_back=True,key_prefix='bank_model'):
    import streamlit as st
    st.subheader('用自然语言办理模拟银行业务')
    st.caption('模型负责理解需求和规划；银行端提供真实模拟账本结果，付款及敏感操作仍需确认或模拟强验证。')
    text=st.text_area('描述需求或补充上一条信息',key=key_prefix+'_input',max_chars=1000,disabled=blocked)
    if st.button('发送需求',key=key_prefix+'_send',disabled=blocked):
        with st.spinner('正在理解需求…'):
            session.submit(text)
    a,b,c,d,e=st.columns(5)
    if a.button('暂停',key=key_prefix+'_pause'):session.pause()
    if b.button('继续草稿',key=key_prefix+'_resume',disabled=blocked):session.resume()
    if c.button('回退草稿',key=key_prefix+'_back',disabled=blocked or not allow_back):session.back()
    if d.button('人工接管',key=key_prefix+'_handoff'):session.handoff()
    if e.button('新需求',key=key_prefix+'_clear',disabled=blocked):session.clear()
    snap=session.snapshot()
    for message in snap['history']:
        if message['role']=='user':
            with st.chat_message('user'):st.text(message['content'])
    if snap['error']:st.warning(snap['error'])
    elif snap['phase']=='clarify':
        # Free model prose is never displayed as a balance, payment receipt or
        # bank fact. Only the bank-operation panel may show those results.
        if snap['intent']=='wealth_assess':st.info('请逐题填写风险问卷答案；可在理财页完成评估后继续。')
        else:st.info('需求还有信息需要核对，请补充下方列出的内容，或说明具体对象和要求。')
    elif snap['phase']=='unsupported':st.info('当前不支持这项模拟银行业务，请明确其他需求或转人工核对。')
    elif snap['phase'] in {'proposal','plan','handoff'} and snap['reply']:st.info(snap['reply'])
    if snap['fields']:
        from app.routing_panel import _label
        extra={'limit_text':'单笔限额','answers':'问卷答案','risk_profile_id':'风险档案编号','holding_id':'持仓编号',
            'purpose':'预留用途','reservation_id':'预留编号','item_id':'商品编号','deliver_on':'配送日期','delivery_address_id':'配送地址编号'}
        st.caption('请核对需求中的信息')
        for key,value in snap['fields'].items():
            st.text(extra.get(key,_label(key))+'：'+str(value))
    if snap['missing']:
        from app.routing_panel import _label
        extra_missing={'answers':'风险问卷答案','risk_profile_id':'风险档案编号','holding_id':'持仓编号','limit_text':'单笔限额',
            'purpose':'预留用途','reservation_id':'预留编号','item_id':'商品编号','deliver_on':'配送日期','delivery_address_id':'地址编号','remind_at':'提醒时间'}
        st.caption('待补充：'+'、'.join(extra_missing.get(m.split(':')[-1],_label(m.split(':')[-1])) for m in snap['missing']))
    if snap['tasks']:
        st.dataframe([{'步骤':t['id'],'任务':t['title'],'先完成':','.join(t['depends_on']) or '无','状态':'尚未执行'} for t in snap['tasks']],hide_index=True)
    if snap['phase'] in {'paused','handoff'}:st.warning('自动推进已停止；已提交的银行操作不会因暂停或回退而撤销。')
    return snap
