"""启动：python -m streamlit run app/main.py。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from agent.controller import Conversation
from agent.demo_gateway import DemoGateway
from shared import ContractError, ServiceError, result_text


st.set_page_config(page_title="FinTechathon · 转账演示", page_icon="💬", layout="wide")


def new_conversation():
    gateway = DemoGateway()
    return Conversation(gateway, principal_id=gateway.principal_id)


if "conversation" not in st.session_state:
    st.session_state.conversation = new_conversation()
conversation = st.session_state.conversation


def perform(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except (ValueError, ContractError, ServiceError) as exc:
        # 用户文案不直接暴露后端错误详情；草稿错误给出可操作的输入提示。
        message = str(exc) if type(exc) is ValueError else "演示调用未完成，请重置演示后重试。"
        conversation.messages.append({"role": "assistant", "content": message})
    st.rerun()


with st.sidebar:
    st.title("FinTechathon")
    st.caption("银行助手 · 本地演示")
    st.divider()
    st.subheader("试一笔转账")
    for index, example in enumerate(("给李明转200元", "给小王转钱", "给小王转两百", "给李明转1200元")):
        if st.button(example, key=f"example_{index}", disabled=conversation.pending, use_container_width=True):
            perform(conversation.submit, example)
    st.caption("小王有两个模拟账户，需要根据尾号选择。")
    st.divider()
    st.write("付款账户")
    st.write("模拟日常账户")
    st.caption("会话刷新后重新开始；本轮不保存银行业务记录。")
    if st.button("重置演示", use_container_width=True):
        st.session_state.conversation = new_conversation()
        st.rerun()

st.caption("A 第一轮交付 · 演示环境")
st.title("把一笔转账说清楚")
st.write("告诉我收款人和金额。信息不完整时，我会先向你确认。")
st.info("此页使用离线规则和测试替身。确认后仅生成模拟回执，账户余额不会变化。")

chat, details = st.columns([1.5, 1], gap="large")
with chat:
    st.subheader("对话")
    if not conversation.messages:
        with st.chat_message("assistant"):
            st.write("可以说“给李明转200元”。也可以先说“给小王转钱”，再补充金额。")
    for message in conversation.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])
    if conversation.candidates and not conversation.request:
        st.write("请选择收款账户")
        for candidate in conversation.candidates:
            label = f"{candidate['display_name']} · {candidate['bank_name']} · {candidate['masked_account']}"
            if st.button(label, key="payee_" + candidate["payee_id"], use_container_width=True):
                perform(conversation.select_payee, candidate["payee_id"])
    prompt = st.chat_input("例如：给小王转200元", key="transfer_input", max_chars=200, disabled=conversation.pending)
    if prompt:
        perform(conversation.submit, prompt)

with details:
    st.subheader("当前操作")
    request, result = conversation.request, conversation.result
    if request and result:
        amount = request["params"]["amount_minor"]
        st.metric("转账金额", f"¥ {amount // 100:,}.{amount % 100:02d}")
        st.write("收款人：" + conversation.payee["display_name"])
        st.write("收款账户：" + conversation.payee["masked_account"])
        st.write("银行：" + conversation.payee["bank_name"])
        st.divider()
        text = result_text(request, result)
        if result["status"] == "succeeded":
            st.success(text)
            st.caption("该回执由测试替身生成，用于验证界面与接口连接。")
        elif result["status"] in {"awaiting_confirmation", "awaiting_verification"}:
            st.warning(text)
            if result["status"] == "awaiting_verification":
                st.write("金额超过1000元，进入模拟强验证流程。")
                if st.button("模拟验证并继续", type="primary", use_container_width=True):
                    perform(conversation.decide, True, simulate_verification=True)
            elif st.button("确认此笔示例转账", type="primary", use_container_width=True):
                perform(conversation.decide, True)
            if st.button("拒绝此操作", use_container_width=True):
                perform(conversation.decide, False)
        else:
            st.info(text)
        with st.expander("查看接口调用示例"):
            st.write("请求")
            st.json(request)
            st.write("返回结果")
            st.json(result)
    elif conversation.draft:
        st.write("收款人：" + conversation.draft["payee_query"])
        st.info("补齐信息或选择收款人后，才会生成转账请求。")
    else:
        st.write("等待你的转账需求。")
        st.caption("准备请求 → 核对详情 → 确认或模拟验证 → 查看示例结果")
