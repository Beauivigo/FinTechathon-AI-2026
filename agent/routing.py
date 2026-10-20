'''Deterministic Chinese routing drafts only; no execution or side effects.'''
import re
from datetime import date

_ACC_RE = re.compile(r'(?<![A-Za-z0-9_])(?:demo_account_[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*|ACCT-[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*)(?![A-Za-z0-9_-])')
_CARD_RE = re.compile(r'(?<![A-Za-z0-9_])CARD-[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*(?![A-Za-z0-9_-])')
_SUB_RE = re.compile(r'(?<![A-Za-z0-9_])SUB-[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*(?![A-Za-z0-9_-])')
_PROD_RE = re.compile(r'(?<![A-Za-z0-9_])PROD-[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*(?![A-Za-z0-9_-])')
_WP_RE = re.compile(r'(?<![A-Za-z0-9_])WP-C[0-9]{2}(?![A-Za-z0-9_-])')
_UNITS_RE = re.compile(r'(?<![A-Za-z0-9_.])([0-9][0-9.,，、/~+＋−\s-]*)份')
_AMOUNT_TOKEN_RE = re.compile(r'(?<![A-Za-z0-9_])[0-9][0-9.,，、/~+＋−\s-]*元')
_AMOUNT_VALUE_RE = re.compile(r'[0-9]+(?:[.][0-9]{1,2})?')
_NEG_BEFORE_RE = re.compile(r'(?:负|[-−－])\s*$')
_RANGE_BEFORE_RE = re.compile(r'[0-9]\s*(?:至|到|或)\s*$')
_DECIMAL_BEFORE_RE = re.compile(r'[.,]\s*$')
_AMOUNT_JOIN_BEFORE_RE = re.compile(r'(?<![0-9])[0-9]+(?:[.][0-9]{1,2})?(?:\s*(?:和|与|及|或者|或|至|到|跟|又|[~～—–−－/／、,，;；+＋-]))+\s*$')
_DT_RE = re.compile(r'(?<![A-Za-z0-9_])([0-9]{4})-([0-9]{2})-([0-9]{2})(?:T|[ ]+)([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?(Z|[+-][0-9]{2}:?[0-9]{2})(?![0-9])')
_DATE_RE = re.compile(r'(?<![A-Za-z0-9_])([0-9]{4})-([0-9]{2})-([0-9]{2})(?![0-9])')
_PERIOD_DASH_RE = re.compile(r'(?<![A-Za-z0-9_])([0-9]{4})-(0[1-9]|1[0-2])(?![0-9-])')
_PERIOD_CN_RE = re.compile(r'(?<![A-Za-z0-9_])([0-9]{4})年(?:([0-9]{1,2})月)?(?![0-9])')
_PAYEE_RE = re.compile(r'(?:给|向)[ ]*([^ ，。！？,;；:：]{1,20}?)[ ]*(?:转账|转|汇款|打款)')
_PAYEE_LABEL_RE = re.compile(r'(?:收款人|收款方|对方|收款账户)[ ]*[:：]?[ ]*([^0-9 ，。！？,;；]{1,20})')
_PART_LABEL_RE = re.compile(r'(?:参与者|参与人|成员|参加人)[ ]*[:：]?[ ]*([^0-9 ，。！？,;；]{1,30})')
_PART_WITH_RE = re.compile(r'(?:和|与|跟|同)[ ]*([^0-9 ，。！？,;；][^ ，。！？,;；]{0,39})')

_CARD_OPS = ('冻结', '解冻', '挂失', '解挂', '申请', '限额')
_SUB_OPS = ('查看', '取消', '提醒', '核实', '排除', '识别')
_SCHED = ('明天', '后天', '定时', '预约')
_EXECUTION_RE = re.compile(r'发卡|批准|审批|核准|审核通过|发货|配送完成|已配送|交付完成|制卡完成|开卡')
_LABELS = {'account_id': '账户', 'period': '账期', 'payee_query': '收款人', 'amount_text': '金额', 'execute_at': '完整带时区的执行时间', 'card_operation': '卡片操作', 'card_id': '卡片', 'subscription_operation': '订阅操作', 'subscription_id': '订阅', 'birthday': '生日(YYYY-MM-DD)', 'participants': '参与人', 'product_ids': '至少两个产品编号', 'product_id': '理财产品或编号（WP-C01—C06）', 'units_text': '赎回份额（份）', 'questionnaire_version': '风险问卷版本'}
_REQ = {'bill': ('account_id', 'period'), 'balance': ('account_id',), 'transfer': ('account_id', 'payee_query', 'amount_text'), 'scheduled_transfer': ('account_id', 'payee_query', 'amount_text', 'execute_at'), 'card': ('card_operation', 'card_id'), 'aa': ('account_id', 'amount_text', 'participants'), 'subscription': ('subscription_operation', 'subscription_id'), 'birthday': ('birthday',), 'products': ('product_ids',), 'wealth_assess': ('questionnaire_version',), 'wealth_purchase': ('account_id', 'product_id', 'amount_text'), 'wealth_redeem': ('account_id', 'units_text'), 'wealth_holdings': ('account_id',)}
def _res(intent, status, fields, missing, message):
    return {'status': status, 'intent': intent, 'fields': fields, 'missing': list(missing), 'message': message}

def _uniq(xs):
    out = []
    for x in xs:
        if x and x not in out:
            out.append(x)
    return out

def _periods(s):
    vals = []
    for m in _PERIOD_DASH_RE.finditer(s):
        vals.append(m.group(1) + '-' + m.group(2))
    for m in _PERIOD_CN_RE.finditer(s):
        y, mo = m.group(1), m.group(2)
        if mo:
            n = int(mo)
            if 1 <= n <= 12:
                vals.append(y + '-' + str(n).zfill(2))
        else:
            vals.append(y)
    return _uniq(vals)

def _dates(s):
    ms = list(_DATE_RE.finditer(s))
    vals = []
    for m in ms:
        try:
            date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        vals.append(m.group(0))
    return ([], True) if len(ms) > 1 else (_uniq(vals), False)

def _datetimes(s):
    ms = list(_DT_RE.finditer(s))
    vals = []
    for m in ms:
        try:
            date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if int(m.group(4)) > 23 or int(m.group(5)) > 59 or int(m.group(6) or 0) > 59:
            continue
        z = m.group(7)
        if z != 'Z' and (int(z[1:3]) > 23 or int(z[-2:]) > 59):
            continue
        vals.append(m.group(0))
    return ([], True) if len(ms) > 1 else (_uniq(vals), False)

def _amounts(s):
    ms = list(_AMOUNT_TOKEN_RE.finditer(s))
    if not ms:
        return [], False
    vals = []
    bad = False
    for m in ms:
        raw = m.group(0)[:-1].rstrip()
        before = s[:m.start()]
        if (_NEG_BEFORE_RE.search(before) or _RANGE_BEFORE_RE.search(before)
                or _AMOUNT_JOIN_BEFORE_RE.search(before)
                or _DECIMAL_BEFORE_RE.search(before)
                or not _AMOUNT_VALUE_RE.fullmatch(raw)
                or not any(ch in '123456789' for ch in raw)):
            bad = True
            continue
        vals.append(raw + '元')
    if bad or len(vals) > 1:
        return [], True
    return vals, False


def _units(s):
    vals=[]
    for m in _UNITS_RE.finditer(s):
        raw=m.group(1).rstrip()
        if (_NEG_BEFORE_RE.search(s[:m.start()]) or _RANGE_BEFORE_RE.search(s[:m.start()])
                or _AMOUNT_JOIN_BEFORE_RE.search(s[:m.start()])
                or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,4})?',raw)
                or not any(ch in '123456789' for ch in raw)):
            return [],True
        vals.append(raw)
    return ([],True) if len(vals)>1 else (vals,False)

def _payees(s):
    vals = []
    for m in _PAYEE_RE.finditer(s):
        v = m.group(1).strip()
        for p in ('收款人', '收款方', '对方'):
            if v.startswith(p):
                v = v[len(p):].lstrip(' :：')
        vals.append(v)
    vals += [m.group(1).strip() for m in _PAYEE_LABEL_RE.finditer(s)]
    vals = _uniq(vals)
    if any(any(c in v for c in '和与、,，或') for v in vals):
        return [], True
    return vals, False

def _participants(s):
    vals = [m.group(1).strip() for m in _PART_LABEL_RE.finditer(s)]
    if not vals:
        for m in _PART_WITH_RE.finditer(s):
            v = m.group(1).strip()
            if v.endswith('一起'):
                v = v[:-2]
            vals.append(v)
    return _uniq(vals)

_NEGATED_OP_RE = re.compile(r'(?:不|别|无需|无须|不用|不必|不需要|毋须)(?:要|想|再|去|用)?\s*(?:查|查询|看|转|转账|汇款|打款|给|冻结|解冻|挂失|解挂|申请|取消|订阅|核实|排除|购买|买|申购|赎回|提醒|准备|办理|执行|操作|支付|付款|扣款)')

def _cancelled(s):
    if '不要' in s or '不想' in s or any(x in s for x in ('取消草稿', '清除草稿', '算了')):
        return True
    for x in ('识别', '区别', '特别', '分别', '告别', '性别', '类别', '级别'):
        s = s.replace(x, '')
    return '别' in s or bool(_NEGATED_OP_RE.search(s))

def _unsupported(s):
    return bool(re.search(r'发行|实物(?:交割|交付|配送|购买)|股票|证券|黄金|保险', s))

def _execution_unsupported(s):
    return bool(_EXECUTION_RE.search(s))

def _intents(s):
    out = set()
    if '账单' in s: out.add('bill')
    if '余额' in s: out.add('balance')
    if re.search(r'转账|转|汇款|打款', s):
        sched = any(k in s for k in _SCHED) or bool(_DATE_RE.search(s))
        out.add('scheduled_transfer' if sched else 'transfer')
    if '卡' in s or ('CARD-' in s and any(op in s for op in _CARD_OPS)): out.add('card')
    if re.search(r'(?i)(?<![A-Za-z0-9_-])aa(?![A-Za-z0-9_-])', s): out.add('aa')
    if '订阅' in s: out.add('subscription')
    if '生日' in s: out.add('birthday')
    wealth = re.search(r'理财|基金|申购|赎回|持仓|风险|问卷', s)
    if wealth:
        trade=False
        if '赎回' in s:
            out.add('wealth_redeem');trade=True
        if re.search(r'申购|买入|买.*(?:理财|基金)', s):
            out.add('wealth_purchase');trade=True
        if not trade:
            out.add('wealth_holdings' if re.search(r'持仓|我的理财',s) else 'wealth_assess')
    if ('产品' in s or '对比' in s) and not wealth: out.add('products')
    return out

def _put(f, key, vals, amb):
    if len(vals) == 1:
        f[key] = vals[0]
    elif len(vals) > 1:
        amb = True
    return amb

def _extract(s, intent):
    f = {}
    amb = False
    if intent == 'bill':
        amb = _put(f, 'account_id', _uniq(_ACC_RE.findall(s)), amb)
        amb = _put(f, 'period', _periods(s), amb)
    elif intent == 'balance':
        amb = _put(f, 'account_id', _uniq(_ACC_RE.findall(s)), amb)
    elif intent in ('transfer', 'scheduled_transfer'):
        amb = _put(f, 'account_id', _uniq(_ACC_RE.findall(s)), amb)
        p, x = _payees(s); amb = _put(f, 'payee_query', p, amb or x)
        a, x = _amounts(s); amb = _put(f, 'amount_text', a, amb or x)
        if intent == 'scheduled_transfer':
            d, x = _datetimes(s); amb = _put(f, 'execute_at', d, amb or x)
    elif intent == 'card':
        amb = _put(f, 'card_operation', [op for op in _CARD_OPS if op in s], amb)
        amb = _put(f, 'card_id', _uniq(_CARD_RE.findall(s)), amb)
        amb = _put(f, 'account_id', _uniq(_ACC_RE.findall(s)), amb)
    elif intent == 'subscription':
        amb = _put(f, 'subscription_operation', [op for op in _SUB_OPS if op in s], amb)
        amb = _put(f, 'subscription_id', _uniq(_SUB_RE.findall(s)), amb)
        amb = _put(f, 'account_id', _uniq(_ACC_RE.findall(s)), amb)
    elif intent == 'birthday':
        d, x = _dates(s); amb = _put(f, 'birthday', d, amb or x)
    elif intent == 'products':
        ids = _uniq(_PROD_RE.findall(s) + _WP_RE.findall(s))
        if ids: f['product_ids'] = ','.join(ids)
    elif intent == 'wealth_assess':
        f['questionnaire_version'] = 'wpq_v1'
        amb = _put(f, 'account_id', _uniq(_ACC_RE.findall(s)), amb)
    elif intent == 'wealth_purchase':
        amb = _put(f, 'account_id', _uniq(_ACC_RE.findall(s)), amb)
        amb = _put(f, 'product_id', _uniq(_WP_RE.findall(s)), amb)
        a, x = _amounts(s); amb = _put(f, 'amount_text', a, amb or x)
    elif intent == 'wealth_redeem':
        amb = _put(f, 'account_id', _uniq(_ACC_RE.findall(s)), amb)
        values,bad=_units(s)
        amb = _put(f, 'units_text', values, amb or bad)
    elif intent == 'wealth_holdings':
        amb = _put(f, 'account_id', _uniq(_ACC_RE.findall(s)), amb)
    elif intent == 'aa':
        amb = _put(f, 'account_id', _uniq(_ACC_RE.findall(s)), amb)
        a, x = _amounts(s); amb = _put(f, 'amount_text', a, amb or x)
        parts = _participants(s)
        if parts: f['participants'] = ','.join(parts)
    if '或' in s:
        amb = True
    return f, amb

def _missing(intent, f):
    if intent == 'card':
        req = ('card_operation', 'account_id') if f.get('card_operation') == '申请' else _REQ['card']
    elif intent == 'subscription':
        req = ('subscription_operation', 'account_id') if f.get('subscription_operation') in ('查看', '识别') else _REQ['subscription']
    else:
        req = _REQ[intent]
    miss = [k for k in req if not f.get(k)]
    if intent == 'products':
        n = len([x for x in str(f.get('product_ids') or '').split(',') if x])
        if n < 2 and 'product_ids' not in miss:
            miss.append('product_ids')
    return miss

def _clarify(intent, fields, missing, amb=False):
    msg = '存在多个可选值，请明确一个。' if amb else ''
    if missing:
        msg += '缺少：' + '、'.join(_LABELS.get(k, k) for k in missing) + '。'
    if not msg:
        msg = '请明确提供所需信息。'
    return _res(intent, 'clarify', fields, missing, msg)

def _ready(intent, fields):
    return _res(intent, 'ready', fields, [], '路由草稿完整（不代表已授权或资源有效）。')

def _single(s, intent):
    f, amb = _extract(s, intent)
    if amb:
        return _clarify(intent, {}, _missing(intent, {}), True)
    miss = _missing(intent, f)
    if miss:
        return _clarify(intent, f, miss)
    return _ready(intent, f)

def _followup(s, draft):
    intent = draft['intent']
    raw = draft.get('fields')
    base = dict(raw) if isinstance(raw, dict) else {}
    new, amb = _extract(s, intent)
    if amb:
        return _clarify(intent, {}, _missing(intent, {}), True)
    if new.get('product_ids'):
        ids = str(base.get('product_ids') or '').split(',') + new['product_ids'].split(',')
        new['product_ids'] = ','.join(_uniq(ids))
    merged = dict(base)
    merged.update(new)
    miss = _missing(intent, merged)
    if not new:
        return _clarify(intent, merged, miss)
    return _ready(intent, merged) if not miss else _clarify(intent, merged, miss)

def route(text, draft=None):
    if not isinstance(text, str):
        return _res(None, 'unsupported', {}, [], '暂不支持该请求。')
    s = text.strip()
    if not s or len(text) > 200:
        return _res(None, 'unsupported', {}, [], '暂不支持该请求。')
    if _cancelled(s):
        return _res(None, 'cancelled', {}, [], '已取消当前路由草稿。')
    if _unsupported(s):
        return _res(None, 'unsupported', {}, [], '暂不支持投资交易或实物交付。')
    if _execution_unsupported(s):
        return _res(None, 'unsupported', {}, [], '暂不支持审批、发卡或配送等执行操作。')
    ints = _intents(s)
    if len(ints) > 1:
        return _res(None, 'clarify', {}, [], '检测到多个意图，请明确一个。')
    if len(ints) == 1:
        return _single(s, next(iter(ints)))
    if isinstance(draft, dict) and draft.get('status') == 'clarify':
        di = draft.get('intent')
        if isinstance(di, str) and di in _REQ:
            return _followup(s, draft)
    return _res(None, 'unsupported', {}, [], '暂不支持该请求。')
