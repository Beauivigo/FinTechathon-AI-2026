"""D 第一轮定版的风险问卷 wpq_v1：5 题必答、1—4 分、总分 5—20 映射 R1—R5。"""

from shared import ServiceError

VERSION = "wpq_v1"

QUESTIONS = {
    "q1": ("投资经验", {"q1_a": 1, "q1_b": 2, "q1_c": 3, "q1_d": 4}),
    "q2": ("收入稳定性", {"q2_a": 1, "q2_b": 2, "q2_c": 3, "q2_d": 4}),
    "q3": ("可投资资产比例", {"q3_a": 1, "q3_b": 2, "q3_c": 3, "q3_d": 4}),
    "q4": ("最大回撤接受度", {"q4_a": 1, "q4_b": 2, "q4_c": 3, "q4_d": 4}),
    "q5": ("投资目标", {"q5_a": 1, "q5_b": 2, "q5_c": 3, "q5_d": 4}),
}

BANDS = ((5, 8, "R1"), (9, 12, "R2"), (13, 16, "R3"), (17, 18, "R4"), (19, 20, "R5"))


def level(total):
    for low, high, risk in BANDS:
        if low <= total <= high:
            return risk
    raise ServiceError("INVALID_REQUEST", "问卷总分超出 5—20。")


def score(answers, version):
    """校验版本、题号、选项与必答，返回 (总分, 风险档)。"""
    if version != VERSION:
        raise ServiceError("INVALID_REQUEST", f"问卷版本必须是 {VERSION}。")
    if type(answers) is not dict:
        raise ServiceError("INVALID_REQUEST", "问卷答案必须是对象。")
    if set(answers) != set(QUESTIONS):
        missing = sorted(set(QUESTIONS) - set(answers))
        extra = sorted(set(answers) - set(QUESTIONS))
        detail = "、".join(filter(None, [
            f"缺少 {','.join(missing)}" if missing else "",
            f"未知题目 {','.join(extra)}" if extra else "",
        ]))
        raise ServiceError("INVALID_REQUEST", f"问卷必须回答 q1—q5：{detail}。")
    total = 0
    for question, (_, choices) in QUESTIONS.items():
        choice = answers[question]
        if type(choice) is not str or choice not in choices:
            raise ServiceError("INVALID_REQUEST", f"{question} 的选项无效。")
        total += choices[choice]
    return total, level(total)
