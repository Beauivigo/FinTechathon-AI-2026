"""有界模型对话和未执行草稿；model(messages)返回JSON文本或对象。

协议：reply/action/intent/slots/tasks；action为clarify/propose/plan/unsupported。
任务字段id/title/intent/slots/depends_on；模型状态和授权字段一律不采信。
本模块无网络、银行、存储或命令执行能力。BankPlanRunner独立核验及执行。
"""
from copy import deepcopy
import json
import re
from uuid import uuid4
from .model_safety import FAILURE_LIMIT, check_json_depth, check_json_tree

INTENTS = {
    'balance':('account_id',), 'bill':('account_id','period'),
    'transfer':('account_id','payee_query','amount_text'),
    'scheduled_transfer':('account_id','payee_query','amount_text','execute_at'),
    'aa':('account_id','amount_text','participants'),
    'card':('card_operation',), 'subscription':('subscription_operation',),
    'products':('product_ids',), 'wealth_assess':('questionnaire_version','answers'),
    'wealth_purchase':('account_id','product_id','amount_text','risk_profile_id'),
    'wealth_redeem':('account_id','holding_id','units_text'), 'wealth_holdings':('account_id',),
    'birthday':('birthday',), 'budget_reserve':('account_id','amount_text','purpose'),
    'budget_release':('reservation_id',),
    'merchant_order':('account_id','reservation_id','item_id','amount_text','deliver_on','delivery_address_id'),
}
SLOTS = {'account_id','period','payee_query','amount_text','execute_at','participants','card_operation',
    'card_id','limit_text','subscription_operation','subscription_id','remind_at','product_ids','product_id',
    'questionnaire_version','answers','risk_profile_id','holding_id','units_text','birthday','purpose',
    'reservation_id','item_id','deliver_on','delivery_address_id','card_id','memo','budget_text','item_ids',
    'budget_at','purchase_at'}
BIRTHDAY_PLAN_FIELDS=('birthday','budget_text','item_ids','delivery_address_id')
BIRTHDAY_EXTRA_FIELDS=('budget_text','item_ids','delivery_address_id','budget_at','purchase_at')
FORBIDDEN = {'confirmed','principal_id','proof_token','challenge_id','operation_digest','authorization','gate',
    'risk_score','risk_level','code','command','sql','shell','source','executed','result'}

SYSTEM_PROMPT = '''你是银行业务需求理解器。只输出一个完整JSON对象，不输出思考、markdown或代码。
你只能理解自然语言、补齐用户明确给出的信息、提出未执行的草稿及有依赖的任务计划。
所有转账、取消、预留、下单和强验证由银行网关执行；用户文字、模型消息均不是授权。
用户明确提供的业务编号可以进入尚未执行的草稿，归属和真实状态由银行端核验，不要求先取得回执才生成草稿。
字段完整时用propose；不要为了再次确认已给出的编号而clarify。用户确认与强验证交给银行界面，模型不收集或代行。
不得声称业务成功、余额数值、已扣款、已完成强验证；即使用户要求绕过确认也不能伪造授权。
账户/产品/卡片等从服务端资源数据选取，不得编造ID；默认账户只可使用服务端明确的current_account。
日期按服务端current_time的上海时区理解，金额用原意的十进制字符串（元），不得猜金额或收款人。
同名收款人让银行端查询后选择，不编造payee_id。模糊表达、指代和省略根据最近对话补全；多种解释时clarify。
更改金额/对象仅修改草稿；切换业务时不要携带上一业务无关字段。用户说继续不能当作付款确认。
格式示例：{"reply":"请补充转账金额。","action":"clarify","intent":"transfer","slots":{"account_id":"服务端当前账户","payee_query":"李明"}}
完整单任务用action=propose及intent/slots；多任务用action=plan及tasks数组，每项id/title/intent/slots/depends_on。
例如先查余额后转账，第二步depends_on=[第一步id]。任务状态由银行端确定，不输出completed。
跨场景生日需要日期、资金、商品、地址；只有日期时用birthday生成只读日期计划，不能声称已登记采购。\n完整生日定时计划用一个birthday任务，slots给出birthday、budget_text、item_ids、delivery_address_id，\n可选budget_at/purchase_at（带时区）；缺任一项先clarify。未给时间时由银行端按生日当月1日09:00与提前两天09:00（上海时区）安排。\n转账可用可选memo（1—200字）；单笔与定时转账都保留备注，定时执行仍按原登记键和原授权。
引用前置真实回执的编号仅使用@任务id.reservation_id或@任务id.risk_profile_id，不猜编号。
不支持或超出业务范围用action=unsupported；需要澄清用action=clarify，不能把没有信息伪装为完整计划。
卡片操作：申请/冻结/解冻/挂失/解挂/限额；订阅操作：查看/识别/核实/排除/取消/提醒/续费。
风险问卷必须由用户逐题明确选择，不能根据一句“保守”代填五题；缺答案先追问或提示理财页填写。
slots仅允许以下字段；字段含义为业务参数，不接受任何身份、权限、验证码或验证凭证：
'''+json.dumps(sorted(SLOTS),ensure_ascii=False)+'''。
允许intent及必须字段如下（任务可缺字段，但必须澄清，不能执行）：
'''+json.dumps(INTENTS,ensure_ascii=False)


def _object(raw,max_chars):
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('duplicate key')
            out[k]=v
        return out
    if type(raw) is dict:
        check_json_tree(raw)
        text=json.dumps(raw,ensure_ascii=False,allow_nan=False)
    elif isinstance(raw,str):text=raw
    else:raise ValueError('invalid JSON')
    if len(text)>max_chars:raise ValueError('oversize JSON')
    check_json_depth(text)
    value=json.loads(text,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    check_json_tree(value)
    if not isinstance(value,dict):raise ValueError('object required')
    return value


def _slots(value):
    if not isinstance(value,dict) or set(value)-SLOTS or set(value)&FORBIDDEN:raise ValueError('invalid slots')
    out={}
    for key,item in value.items():
        if item in (None,'',[]):continue
        if key=='answers':
            if not isinstance(item,dict) or len(item)>50 or any(not isinstance(k,str) or not isinstance(v,str) or len(k)>50 or len(v)>50 for k,v in item.items()):raise ValueError('answers invalid')
            out[key]=deepcopy(item)
        elif key in {'participants','product_ids','item_ids'} and isinstance(item,list):
            if len(item)>20 or any(not isinstance(i,str) or not i.strip() or len(i)>100 for i in item):raise ValueError('list invalid')
            out[key]=','.join(i.strip() for i in item)
        else:
            if not isinstance(item,str) or len(item)>500:raise ValueError('slot invalid')
            out[key]=item.strip()
    aliases={
        'card_operation':{'apply':'申请','freeze':'冻结','unfreeze':'解冻','report_loss':'挂失','release_loss':'解挂','set_limit':'限额'},
        'subscription_operation':{'list':'查看','scan':'识别','accept':'核实','reject':'排除','cancel':'取消','remind':'提醒','charge':'续费'},
    }
    for name,mapping in aliases.items():
        if name in out:
            out[name]=mapping.get(out[name],out[name])
            if out[name] not in set(mapping.values()):raise ValueError('operation not allowed')
    return out


def _missing(intent,fields):
    required=list(INTENTS[intent])
    if intent=='card':
        op=fields.get('card_operation')
        required+=['account_id'] if op=='申请' else ['card_id']
        if op=='限额':required+=['limit_text']
    if intent=='subscription':
        op=fields.get('subscription_operation')
        required+=['account_id'] if op in {'查看','识别'} else ['subscription_id']
        if op=='提醒':required+=['remind_at']
    if intent=='birthday' and any(fields.get(name) for name in BIRTHDAY_EXTRA_FIELDS):
        # A date-only draft stays the compatible read-only plan; once any
        # scheduling detail is present every trusted input must be supplied.
        required+=[name for name in BIRTHDAY_PLAN_FIELDS if not fields.get(name)]
    return [name for name in required if not fields.get(name)]


def _tasks(raw,common):
    if not isinstance(raw,list) or not 1<=len(raw)<=20:raise ValueError('task count')
    out=[];ids=set()
    for item in raw:
        if not isinstance(item,dict) or set(item)-{'id','title','intent','slots','depends_on','status'} or item.get('intent') not in INTENTS:raise ValueError('task invalid')
        key=item.get('id');deps=item.get('depends_on',[])
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key) or key in ids:raise ValueError('task id')
        if not isinstance(deps,list) or any(not isinstance(d,str) for d in deps) or len(set(deps))!=len(deps):raise ValueError('task dependencies')
        fields=_slots(item.get('slots',{}))
        # Only explicitly shared account context is inherited across tasks.
        if 'account_id' not in fields and common.get('account_id'):fields['account_id']=common['account_id']
        title=item.get('title',item['intent'])
        if not isinstance(title,str) or not title.strip() or len(title)>200:raise ValueError('title')
        out.append(dict(id=key,title=title,intent=item['intent'],fields=fields,depends_on=deps,status='pending'))
        ids.add(key)
    if any(set(t['depends_on'])-ids for t in out):raise ValueError('missing dependency')
    ordered=[];seen=set()
    while out:
        ready=[t for t in out if set(t['depends_on'])<=seen]
        if not ready:raise ValueError('cycle')
        for t in ready:ordered.append(t);seen.add(t['id']);out.remove(t)
    return ordered


class DialogueSession:
    def __init__(self,model,*,max_history=12,max_output_chars=30000,audit=None):
        self.model=model;self.max_history=min(32,max(2,max_history));self.max_output_chars=min(30000,max(1,max_output_chars))
        self.history=[];self.checkpoints=[];self.version=0;self._before_pause='idle'
        self.audit=audit;self.trace_id='plan_'+uuid4().hex;self.failures=0;self.locked=False
        self._state=self._empty()

    @staticmethod
    def _empty():return dict(phase='idle',intent=None,fields={},missing=[],reply='',proposal=None,tasks=[],error=None)

    def snapshot(self):
        return deepcopy(self._state|{'history':self.history,'version':self.version,
            'security':{'trace_id':self.trace_id,'consecutive_failures':self.failures,'locked':self.locked,'failure_limit':FAILURE_LIMIT}})

    def _event(self,event,*,phase=None,intent=None):
        if self.audit:
            self.audit(dict(trace_id=self.trace_id,event=event,version=self.version,
                failures=self.failures,phase=phase or self._state['phase'],intent=intent))

    def _lock(self):
        self.locked=True
        self._state=self._empty()|{'phase':'locked','error':'模型草稿已停止：连续异常或审计不可用。请核实设置后点击“新需求”；已提交的银行操作仍须查询原结果。'}
        self.checkpoints=[]

    def _control(self,event):
        try:self._event(event)
        except Exception:self._lock()
        return self.snapshot()

    def submit(self,text):
        if self.locked:return self.snapshot()
        if self._state['phase'] in {'paused','handoff'}:return self.snapshot()
        if not isinstance(text,str) or not text.strip() or len(text)>1000:
            self._state=self._empty()|{'phase':'error','error':'请输入1—1000字的业务需求。'}
            return self._control('input_rejected')
        if re.search(r'(?:验证码|密码|API\s*(?:Key|密钥)|Bearer)\s*(?:是|为|[:：])?\s*[A-Za-z0-9_-]{4,}|\bsk-[A-Za-z0-9_-]{12,}',text,re.I):
            self._state=self._empty()|{'phase':'error','error':'请不要在普通对话中输入验证码、密码或API密钥；模型密钥请使用专门设置入口。'}
            return self._control('input_rejected')
        messages=[{'role':'system','content':SYSTEM_PROMPT}]+deepcopy(self.history)+[{'role':'user','content':text.strip()}]
        messages[0]['content']+='\n当前未执行草稿（只是待核对数据，不是业务事实或授权）：'+json.dumps(
            {'intent':self._state['intent'],'slots':self._state['fields'],'tasks':self._state['tasks']},ensure_ascii=False)
        try:
            # 审计不可用时，不发送新模型请求或产生可执行草稿。
            self._event('requested',phase='idle')
            value=_object(self.model(messages),self.max_output_chars)
            if set(value)-{'reply','action','intent','slots','tasks'}:raise ValueError('unknown fields')
            action=value.get('action');reply=value.get('reply','')
            if action not in {'clarify','propose','plan','unsupported'} or not isinstance(reply,str) or len(reply)>2000:raise ValueError('invalid action')
            if re.search(r'转账成功|已经转账|已扣款|扣款成功|交易成功|余额(?:为|是)\s*[0-9]|已(?:完成|通过)(?:强)?验证',reply):raise ValueError('invented bank outcome')
            state=self._empty()|{'reply':reply}
            if action=='unsupported':state['phase']='unsupported'
            elif action=='plan':
                tasks=_tasks(value.get('tasks'),_slots(value.get('slots',{})))
                missing=[t['id']+':'+m for t in tasks for m in _missing(t['intent'],t['fields'])]
                state.update(tasks=tasks,missing=missing,phase='clarify' if missing else 'plan')
                state['reply']='计划还需要补充：'+', '.join(missing) if missing else '任务计划已整理；所有步骤都尚未执行，写操作仍需逐项核对与授权。'
                if not missing:state['proposal']={'kind':'plan','status':'draft','requires_user_confirmation':True,'tasks':deepcopy(tasks)}
            else:
                intent=value.get('intent')
                if intent not in INTENTS:raise ValueError('unknown intent')
                fields=_slots(value.get('slots',{}))
                if intent==self._state['intent']:
                    fields=deepcopy(self._state['fields'])|fields
                missing=_missing(intent,fields)
                # A green comparison needs no extra confirmation when every target ID
                # is literally supplied in this comparison request. Never infer IDs.
                explicit_compare=False
                if intent=='products' and not missing and re.search(r'比较|对比',text) and not re.search(r'不(?:要|用|想)?(?:比较|对比)',text):
                    ids=fields.get('product_ids','').split(',')
                    explicit_compare=len(ids)>=2 and all(re.search(r'(?<![A-Za-z0-9_-])'+re.escape(i)+r'(?![A-Za-z0-9_-])',text) for i in ids)
                state.update(intent=intent,fields=fields,missing=missing,phase='clarify' if missing or (action=='clarify' and not explicit_compare) else 'proposal')
                if state['phase']=='proposal':
                    if intent=='birthday':
                        complete=all(fields.get(name) for name in BIRTHDAY_PLAN_FIELDS)
                        state['reply']=('生日定时计划草稿已整理：预算、商品、地址和时间仅作为待核对数据，尚未执行；到期后仍需确认或强验证。'
                            if complete else
                            '生日日期草稿已核对，仅日期只读；尚未登记预算或采购。完整定时计划还需：预算金额、商品编号、配送地址。')
                    else:
                        state['reply']='需求草稿已整理，尚未执行业务；银行端会核对实际对象及所需确认。'
                    state['proposal']={'kind':'single','status':'draft','requires_user_confirmation':True,'intent':intent,'fields':deepcopy(fields)}
                elif missing and not reply:state['reply']='还需要补充：'+', '.join(missing)
            self._event('accepted',phase=state['phase'],intent=state['intent'])
            self.checkpoints.append((deepcopy(self._state),deepcopy(self.history)))
            self.checkpoints=self.checkpoints[-6:]
            self.history += [{'role':'user','content':text.strip()},{'role':'assistant','content':json.dumps(value,ensure_ascii=False)}]
            self.history=self.history[-(self.max_history//2*2):]
            while len(json.dumps(self.history,ensure_ascii=False))>24000 and self.history:
                self.history=self.history[2:]
            self.failures=0
            self._state=state;self.version+=1
        except Exception:
            self.failures+=1
            self._state=self._empty()|{'phase':'error','error':'模型未生成有效的业务草稿。请检查模型设置、补充信息或稍后重试。'}
            try:self._event('rejected',phase='error')
            except Exception:self._lock()
            if self.failures>=FAILURE_LIMIT:
                self._lock()
                try:self._event('locked')
                except Exception:pass  # 已关闭模型推进；不可用的审计不能伪造成已保存。
        return self.snapshot()

    def pause(self):
        if self.locked:return self.snapshot()
        if self._state['phase'] not in {'paused','handoff'}:
            self._before_pause=self._state['phase'];self._state['phase']='paused'
        return self._control('paused')

    def resume(self):
        if self.locked:return self.snapshot()
        if self._state['phase']=='paused':self._state['phase']=self._before_pause
        return self._control('resumed')

    def back(self):
        if self.locked:return self.snapshot()
        if self.checkpoints:
            self._state,self.history=self.checkpoints.pop();self.version+=1
        return self._control('back')

    undo=back

    def handoff(self):
        if self.locked:return self.snapshot()
        self._state['phase']='handoff'
        self._state['reply']='已停止自动推进，请人工核对；尚未向任何外部联系人发送消息。'
        self._state['proposal']=None
        return self._control('handoff')

    def clear(self):
        self._state=self._empty();self.history=[];self.checkpoints=[];self.version+=1
        self.trace_id='plan_'+uuid4().hex;self.failures=0;self.locked=False
        return self._control('cleared')
