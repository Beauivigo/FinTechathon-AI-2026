"""User-facing yuan helpers.

The bank contract and all fund math stay in integer minor units.  These
helpers only format costs for display and convert explicitly entered yuan
strings at the UI edge; they never replace the shared validators or do money
arithmetic with binary floats.
"""
import re

import streamlit as st

from shared import yuan_to_minor

MAX_SAFE_MINOR = 9007199254740991


def yuan_text(minor):
    """Render an integer minor amount as yuan with exactly two decimals."""
    value = int(minor)
    sign = '-' if value < 0 else ''
    value = abs(value)
    return f'{sign}{value // 100:,}.{value % 100:02d}元'


def parse_yuan(value, *, allow_zero=False):
    """Validate a UI yuan value and return integer minor units.

    Raises ValueError with an actionable message; never rounds to two
    decimals.  None means the user has not filled the field yet.
    """
    if value is None or isinstance(value, bool):
        raise ValueError('请输入金额。')
    raw = str(value).strip()
    if len(raw) > 20 or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', raw):
        raise ValueError('金额请填写数字，最多两位小数，不会自动舍入。')
    amount = yuan_to_minor(raw)
    if not allow_zero and amount == 0:
        raise ValueError('金额必须大于 0。')
    if amount > MAX_SAFE_MINOR:
        raise ValueError('金额超出允许范围。')
    return amount


def amount_input(label, key, *, help=None, allow_zero=False):
    """Empty-by-default yuan input; callers decide whether zero is valid."""
    return st.text_input(label, value='', max_chars=20, key=key,
                         help=help or '单位：元，最多两位小数。例如 200.01。')
