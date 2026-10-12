"""离线规则演示，只识别普通转账；不调用模型或执行业务。"""

import re

from shared import ContractError, yuan_to_minor


def amount_string(text):
    text = re.sub(r"(?:元|块钱|块)$", "", text)
    if re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", text):
        value = text
    elif re.fullmatch(r"[零〇一二两三四五六七八九十百千]+", text):
        digits = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
        units = {"十": 10, "百": 100, "千": 1000}
        total, digit, last_unit, zero = 0, None, 10000, False
        for char in text:
            if char in digits:
                if digits[char] == 0:
                    if digit is not None or last_unit == 10000 or zero:
                        raise ValueError("金额请用数字填写，例如200或200.50元。")
                    zero = True
                else:
                    if digit is not None:
                        raise ValueError("金额请用数字填写，例如200或200.50元。")
                    digit = digits[char]
            else:
                unit = units[char]
                if unit >= last_unit or zero or (digit is None and not (unit == 10 and total == 0)):
                    raise ValueError("金额请用数字填写，例如200或200.50元。")
                total += (1 if digit is None else digit) * unit
                digit, last_unit = None, unit
        if zero and digit is None or (digit is not None and last_unit >= 100 and last_unit != 10000 and not zero):
            raise ValueError("金额有歧义，请用数字填写，例如250元。")
        value = str(total + (digit or 0))
    else:
        raise ValueError("金额请用数字填写，例如200或200.50元。")
    try:
        if yuan_to_minor(value) <= 0:
            raise ValueError("转账金额需要大于0元。")
    except ContractError as exc:
        raise ValueError("金额格式不正确，请填写正金额，最多两位小数。") from exc
    return value


def parse_transfer(text, draft=None):
    """返回未执行的草稿；金额缺失为 None，不猜测用户未提供的内容。"""
    if type(text) is not str or not text.strip() or len(text) > 200:
        raise ValueError("请输入200字以内的转账需求。")
    text = re.sub(r"\s+", "", text.strip()).rstrip("。！!？?")
    match = re.fullmatch(r"(?:请)?(?:帮我)?(?:给|向)([\u4e00-\u9fffA-Za-z·]{1,20}?)(?:转账|转|汇款)(.*)", text)
    if match:
        name, amount = match.groups()
        return {"payee_query": name, "amount_yuan": None if amount in ("", "钱") else amount_string(amount)}
    if draft and re.fullmatch(r"[0-9零〇一二两三四五六七八九十百千.]+(?:元|块钱|块)?", text):
        return {"payee_query": draft["payee_query"], "amount_yuan": amount_string(text)}
    raise ValueError("此演示支持普通转账，例如“给李明转200元”或“给小王转钱”。")
