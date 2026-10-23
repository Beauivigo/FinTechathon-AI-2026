"""结构化计划的技术边界；不执行生成代码，不改变银行业务规则。"""
import re

MAX_DEPTH = 16
MAX_NODES = 4096
FAILURE_LIMIT = 5  # 模型草稿连续失败的会话熔断，与银行五次验证锁定相互独立。
EVENTS = frozenset({'requested', 'input_rejected', 'accepted', 'rejected', 'locked', 'paused', 'resumed', 'back', 'handoff', 'cleared', 'step_result'})
PHASES = frozenset({'idle', 'clarify', 'proposal', 'plan', 'unsupported', 'error', 'paused', 'handoff', 'locked'})


def check_trace(value):
    if not isinstance(value, str) or not re.fullmatch(r'plan_[a-f0-9]{32}', value):
        raise ValueError('invalid trusted plan trace')
    return value


def check_json_tree(value):
    """先限制结构，再序列化；循环、深嵌套及非JSON对象均被有界拒绝。"""
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError('JSON resource limit')
        if type(item) is dict:
            if len(item) > MAX_NODES or any(type(k) is not str for k in item):
                raise ValueError('invalid JSON object')
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            if len(item) > MAX_NODES:
                raise ValueError('JSON resource limit')
            pending.extend((v, depth + 1) for v in item)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ValueError('non JSON value')
        elif type(item) is str and len(item) > 30000:
            raise ValueError('JSON string limit')


def check_json_depth(text):
    """在JSON解析器前限制容器嵌套，字符串内的括号不计入深度。"""
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == '"': quoted = False
        elif char == '"': quoted = True
        elif char in '[{':
            depth += 1
            if depth > MAX_DEPTH: raise ValueError('JSON depth limit')
        elif char in ']}': depth -= 1
