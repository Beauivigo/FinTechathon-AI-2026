"""Rewrite slogan-style deck copy into a factual, report-like register.

Matching ignores the manual line breaks (\\x0b/\\n) that some titles carry; the
first run keeps its formatting and the remaining runs are dropped.
"""
import sys
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "言行_FinTechathon_2026_24页_D修订_v2.pptx")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "言行_FinTechathon_2026_24页_D修订_v3.pptx")

RENAMES = {
    "一句话，把“想办的事”变成可授权、可执行、可追溯的银行任务。": "输入一句话，生成可授权、可执行、可追溯的银行任务。",
    "一条可信主干": "统一执行主干",
    "银行不缺入口，缺的是一条能被信任的完成路径": "现状：入口分散，执行过程缺少可核对路径",
    "用户要的是办成，不是更多按钮。": "评价指标为业务完成率。",
    "不是会聊天的页面，而是受控任务的执行层": "系统定位：受控任务的执行层",
    "六个高频场景，共用一条可信执行主干": "六类高频场景共用一条执行主干",
    "一套身份、授权、账本、审计，不是六个孤岛。": "身份、授权、账本与审计由六类场景共用。",
    "四层架构：不确定性留在模型层，确定性交给执行层": "四层架构：模型层承担不确定性，执行层保证确定性",
    "只监听 127.0.0.1 · 单用户本机 · 本地模拟银行，不是生产系统": "仅监听 127.0.0.1，单用户本机运行，属模拟系统",
    "DeepSeek 是默认大脑，但不是资金决策者": "DeepSeek 为默认模型，不参与资金决策",
    "模型输出是草稿，不是授权凭证。": "模型输出不构成授权凭证。",
    "绿黄红不是颜色，是三种授权成本": "三级授权：查询、确认与强验证",
    "动态：定时转账到期重新授权；登记不是永久授权。": "定时转账到期需重新授权（登记非永久授权）。",
    "五道防线，让一次 AI 操作可以被信任": "五类约束构成一次操作的可信边界",
    "通过用例不等于零漏洞；边界如实标注，才能被信任。": "用例通过不代表无缺陷；边界按实际标注。",
    "六个场景不是六个 Demo，而是六种业务对象": "六类业务对象共用同一套账本与授权",
    "到期重授权；AA 建单不等于收齐": "到期需重新授权；AA 建单不表示收齐",
    "异常规则不等于完整反欺诈": "异常规则不构成完整反欺诈",
    "受理不等于审批或发卡": "受理不代表审批或发卡",
    "预留不等于扣款；到期仍授权": "预留不代表扣款；到期仍需授权",
    "转账：先把人认对，再把钱转对": "转账：收款人识别与金额核对",
    "登记不是永久授权；到期重新计算要求。": "登记不构成永久授权；到期重新计算要求。",
    "建单不等于收齐；每笔付款仍需授权。": "建单不代表收齐；每笔付款仍需授权。",
    "unknown 先查询原操作，不直接换新键；NOT_FOUND 不等于未扣款。": "unknown 先查询原操作，不更换键；NOT_FOUND 不代表未扣款。",
    "账单：把流水变成可解释的结构": "账单：流水分类与支出解释",
    "异常识别是既定规则，不是完整欺诈识别。": "异常识别为既定规则，非完整欺诈识别。",
    "理财：先评估风险，再允许申购": "理财：风险评估与申赎规则",
    "卡片：从申请受理到状态管理，每一步都可拒绝": "卡片：申请受理与状态管理",
    "受理不是发卡；状态决定能做什么。": "受理不代表发卡，状态决定操作",
    "订阅与生日：把“以后”变成可撤销的计划": "订阅与生日：周期扣费与预算计划",
    "候选不是已订阅": "候选非已订阅",
    "取消不等于退款": "取消非退款",
    "站内记录，不是推送": "站内记录，非推送",
    "登记不是付款": "登记非付款",
    "预留不是扣款": "预留非扣款",
    "计划不是永久授权；预留不是扣款；通知不是送达。": "计划非永久授权；预留非扣款；通知非送达。",
    "530 到 713：测试增长的是边界，不是数字": "回归测试由 530 项扩展至 713 项",
    "演示不是念 PPT，是走一条信任链。": "演示按业务流程逐步执行。",
    "5 分钟，走完一条完整的信任链": "五分钟完成一次完整业务演示",
    "第一屏：先让用户知道“我在哪、能做什么”": "第一屏：账户状态与可用操作",
    "第二屏：一句话之后，系统先补信息再让用户确认": "第二屏：自然语言输入与信息补全",
    "第三屏：红色操作，必须让“这一次”被证明": "第三屏：强验证与操作回执",
    "完成的是可运行的本地闭环，不是一家真实银行": "交付范围：本地可运行闭环",
    "本地模拟 · 不是生产银行；边界写清楚，才值得信任。": "本地模拟环境，非生产银行；边界按实际标注。",
    "五个人，一条从规则到交付的责任链": "五人分工与责任边界",
    "把 AI 关进笼子，才敢让它替你办事": "AI 权限边界与后续接入计划",
    "下一步：接入真实银行沙箱环境。": "下一步：接入真实银行沙箱。",
    "下一步：真实银行沙箱接入，而不是更多 Demo。": "下一步：接入真实银行沙箱。",
    "模型可以提议，": "模型负责提议，",
    "不能签字。": "程序签字。",
    "会编、会越权、会说不清；越接近钱，越不敢让它自己做。": "存在编造与越权风险，资金操作不宜交由模型。",
    "一条信任链": "执行主干",
    "越接近钱，授权越贵。": "资金操作需强验证",
    "让 AI 敢用、能用、可追责。": "AI 使用受控且可追溯。",
    "但它是本地模拟。": "当前为本地模拟。",
}


def norm(text):
    return text.replace("\x0b", "").replace("\n", "").strip()


NORMALIZED = {norm(key): (key, value) for key, value in RENAMES.items()}


def leaves(shapes):
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from leaves(shape.shapes)
        else:
            yield shape


def set_text(shape, text):
    paragraph = shape.text_frame.paragraphs[0]
    paragraph.runs[0].text = text
    for run in paragraph.runs[1:]:
        run._r.getparent().remove(run._r)


prs = Presentation(str(SRC))
used = set()
for index, slide in enumerate(prs.slides, 1):
    for shape in leaves(slide.shapes):
        if not shape.has_text_frame:
            continue
        hit = NORMALIZED.get(norm(shape.text_frame.text))
        if hit is None:
            continue
        key, replacement = hit
        set_text(shape, replacement)
        used.add(norm(key))
        print(f"s{index:02d} {shape.name}")

missing = sorted(set(NORMALIZED) - used)
if missing:
    print("UNMATCHED:")
    for item in missing:
        print(" -", NORMALIZED[item][0])

prs.save(str(OUT))
print("saved", OUT, f"| replacements {len(used)}/{len(NORMALIZED)}")
