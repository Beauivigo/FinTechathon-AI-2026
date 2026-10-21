"""不信任模型提案：白名单映射、可信对象解析、现有网关执行与逐步恢复。"""
from copy import deepcopy
from datetime import date
from calendar import monthrange
from decimal import Decimal, InvalidOperation
import re

from shared import adapt_request, validate_result, ContractError, ServiceError, result_text
from shared.adapters import yuan_to_minor
from uuid import uuid4


class NeedsInput(ValueError):
    def __init__(self, message, choices=None):
        super().__init__(message)
        self.choices = choices or []


def _money(value,*,allow_zero=False):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?元?',value.strip()):
        raise NeedsInput('请给出准确的正数金额，最多两位小数。')
    amount=yuan_to_minor(value.strip().removesuffix('元'))
    if amount<0 or (amount==0 and not allow_zero):raise NeedsInput('金额必须大于零。')
    return amount


def _units(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,4})?份?',value.strip()):
        raise NeedsInput('请给出准确的正数份额，最多四位小数。')
    raw=value.strip().removesuffix('份')
    try:
        if Decimal(raw)<=0:raise NeedsInput('赎回份额必须大于零。')
    except InvalidOperation:
        raise NeedsInput('赎回份额无效。') from None
    return raw


def query(flow,action,params):
    req=adapt_request(action,params,request_id='query_'+uuid4().hex,trace_id='model_read_'+uuid4().hex)
    result=validate_result(req,flow.gateway.execute(req,principal_id=flow.principal_id))
    if result['status']!='succeeded':raise NeedsInput('当前无法核实业务数据，请先处理或查询原操作。')
    return result['data']


def _account(fields,resources):
    accounts=resources['accounts']
    chosen=fields.get('account_id')
    if not chosen and len(accounts)==1:chosen=accounts[0]['account_id']
    if chosen not in {a['account_id'] for a in accounts}:raise NeedsInput('请从当前用户账户中明确选择账户。')
    return chosen


def _payee(flow,name,selected=None):
    if not isinstance(name,str) or not name.strip():raise NeedsInput('请说明收款人姓名或手机号。')
    candidates=query(flow,'payee.resolve',{'query':name,'match_type':'phone' if re.fullmatch(r'[0-9]{11}',name) else 'name'})['candidates']
    if selected:
        candidates=[c for c in candidates if c['payee_id']==selected]
    if len(candidates)!=1:raise NeedsInput('收款人不唯一，请核对并选择具体收款账户。',candidates)
    return candidates[0]['payee_id']


def compile_task(flow,intent,fields,*,selected_payee=None,results=None):
    """只生成既有接口请求；缺字段不补猜，权限由BankGateway决定。"""
    if not isinstance(fields,dict):raise NeedsInput('业务参数格式无效。')
    forbidden={'principal_id','confirmed','proof_token','challenge_id','operation_digest','decision','risk_level','risk_score','gate','authorization','source'}
    if forbidden & set(fields):raise NeedsInput('模型不能提供身份、授权、风险评分或验证凭证。')
    resources=flow.gateway.get_context(principal_id=flow.principal_id)
    p=deepcopy(fields)
    for key,value in p.items():
        if isinstance(value,str) and value.startswith('@'):
            match=re.fullmatch(r'@([A-Za-z0-9_-]{1,64})\.(reservation_id|risk_profile_id)',value)
            if not match or key!=match[2]:raise NeedsInput('任务引用无效，只能引用前置真实回执中的对应编号。')
            receipt=(results or {}).get(match[1],{})
            if receipt.get('status')!='succeeded' or key not in receipt.get('data',{}):raise NeedsInput('前置业务尚未返回可信编号。')
            p[key]=receipt['data'][key]
    def account():return _account(p,resources)
    def own_object(group,key):
        value=p.get(key)
        if value not in {r[key] for r in resources.get(group,[])}:raise NeedsInput('请明确选择当前用户的业务对象。')
        return value
    if intent=='balance':return 'account.balance',{'account_id':account()}
    if intent=='bill':
        period=p.get('period','')
        if re.fullmatch(r'[0-9]{4}-[0-9]{2}',period):
            year,month=map(int,period.split('-'))
            start=date(year,month,1);end=date(year,month,monthrange(year,month)[1])
        elif re.fullmatch(r'[0-9]{4}',period):start=date(int(period),1,1);end=date(int(period),12,31)
        else:raise NeedsInput('请说明账单月份或年份，例如2026-10。')
        return 'bill.analyze',{'account_id':account(),'start_date':start.isoformat(),'end_date':end.isoformat()}
    if intent in {'transfer','scheduled_transfer'}:
        params={'account_id':account(),'payee_id':_payee(flow,p.get('payee_query'),selected_payee),'amount_minor':_money(p.get('amount_text'))}
        if intent=='scheduled_transfer':
            if not p.get('execute_at'):raise NeedsInput('请给出带时区的预约时间。')
            params['execute_at']=p['execute_at']
        return ('transfer.schedule' if intent=='scheduled_transfer' else 'transfer.create'),params
    if intent=='aa':
        names=p.get('participants',[])
        if isinstance(names,str):names=re.split(r'[,，、]',names)
        if not isinstance(names,list) or not names or any(not isinstance(n,str) or not n.strip() for n in names):raise NeedsInput('请明确所有AA参与人。')
        return 'transfer.split',{'account_id':account(),'payee_ids':[_payee(flow,n.strip()) for n in names],'amount_minor':_money(p.get('amount_text'))}
    if intent=='card':
        op=p.get('card_operation')
        if op=='申请':return 'card.apply',{'account_id':account()}
        ops={'冻结':'card.freeze','解冻':'card.unfreeze','挂失':'card.report_loss','解挂':'card.release_loss','限额':'card.set_limit'}
        if op not in ops:raise NeedsInput('请明确卡片操作。')
        params={'card_id':own_object('cards','card_id')}
        if op=='限额':params['limit_minor']=_money(p.get('limit_text',p.get('amount_text')),allow_zero=True)
        return ops[op],params
    if intent=='subscription':
        op=p.get('subscription_operation')
        if op in {'查看','识别'}:return ('subscription.list' if op=='查看' else 'subscription.scan'),{'account_id':account()}
        # subscription records are returned through the yellow read, not leaked via context.
        if not isinstance(p.get('subscription_id'),str):raise NeedsInput('请先核对并提供订阅编号。')
        params={'subscription_id':p['subscription_id']}
        if op in {'取消','续费','本期续费'}:return ('subscription.cancel' if op=='取消' else 'subscription.charge'),params
        if op in {'核实','排除'}:return 'subscription.review',params|{'decision':'accept' if op=='核实' else 'reject'}
        if op=='提醒' and p.get('remind_at'):return 'subscription.remind',params|{'remind_at':p['remind_at']}
        raise NeedsInput('请明确订阅操作及所需时间。')
    if intent=='products':
        ids=p.get('product_ids',[])
        if isinstance(ids,str):ids=[s.strip() for s in re.split(r'[,，、]',ids)]
        if not isinstance(ids,list) or len(ids)<2 or any(i not in {r['product_id'] for r in resources['products']} for i in ids):raise NeedsInput('请选择至少两个已有产品编号进行对比。')
        return 'wealth.compare',{'product_ids':ids}
    if intent=='wealth_assess':
        if not isinstance(p.get('answers'),dict):raise NeedsInput('请到理财页逐题完成风险问卷，模型不能替你填写风险答案。')
        return 'wealth.assess',{'questionnaire_version':p.get('questionnaire_version','wpq_v1'),'answers':p['answers']}
    if intent=='wealth_purchase':
        risk=p.get('risk_profile_id')
        if not risk:raise NeedsInput('请先完成风险问卷并核对风险档案编号。')
        return 'wealth.purchase',{'account_id':account(),'product_id':own_object('products','product_id'),'risk_profile_id':risk,'amount_minor':_money(p.get('amount_text'))}
    if intent=='wealth_redeem':
        return 'wealth.redeem',{'account_id':account(),'holding_id':own_object('holdings','holding_id'),'units':_units(p.get('units_text'))}
    if intent=='wealth_holdings':
        chosen=account()
        return '__holdings__',{'account_id':chosen,'holdings':[h for h in resources.get('holdings',[]) if h['account_id']==chosen]}
    if intent=='birthday':
        birthday=p.get('birthday')
        if not isinstance(birthday,str):raise NeedsInput('请明确生日日期。')
        date.fromisoformat(birthday)
        return 'scenario.plan',{'goal':'生日 '+birthday}
    if intent=='budget_reserve':
        if not isinstance(p.get('purpose'),str) or not p['purpose'].strip():raise NeedsInput('请说明预留用途。')
        return 'budget.reserve',{'account_id':account(),'amount_minor':_money(p.get('amount_text')),'purpose':p['purpose']}
    if intent=='budget_release':
        if not isinstance(p.get('reservation_id'),str):raise NeedsInput('请明确已有预留编号。')
        return 'budget.release',{'reservation_id':p['reservation_id']}
    if intent=='merchant_order':
        item=own_object('items','item_id');address=own_object('addresses','delivery_address_id')
        if not isinstance(p.get('reservation_id'),str) or not isinstance(p.get('deliver_on'),str):raise NeedsInput('请明确预留编号与配送日期。')
        params={'account_id':account(),'reservation_id':p['reservation_id'],'item_id':item,
            'amount_minor':_money(p.get('amount_text')),'deliver_on':p['deliver_on'],'delivery_address_id':address}
        if p.get('card_id'):params['card_id']=own_object('cards','card_id')
        return 'merchant.order',params
    raise NeedsInput('当前没有这个可执行业务，请转人工核对。')


class BankPlanRunner:
    """模型不接触实例；每步使用原Conversation的确认、强验证及恢复。"""
    def __init__(self,flow,tasks):
        self.flow=flow
        self.tasks=deepcopy(tasks)
        self.receipts={}
        self.paused=False
        self.waiting=None
        self.choices={}
        self.integrity_blocked=False
        for t in self.tasks:
            t['status']='pending'
        self._validate_graph()

    def _validate_graph(self):
        if not 1<=len(self.tasks)<=20:raise NeedsInput('计划最多允许20步。')
        ids=[t.get('id') for t in self.tasks]
        if any(not isinstance(i,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',i) for i in ids) or len(set(ids))!=len(ids):raise NeedsInput('任务编号无效。')
        seen=set()
        pending=list(self.tasks)
        ordered=[]
        while pending:
            ready=[t for t in pending if isinstance(t.get('depends_on'),list) and all(d in seen for d in t['depends_on'])]
            if not ready:raise NeedsInput('任务依赖缺失或存在循环。')
            for task in ready:
                seen.add(task['id']);ordered.append(task);pending.remove(task)
        self.tasks=ordered

    def _sync(self):
        if not self.waiting:return
        task=next(t for t in self.tasks if t['id']==self.waiting)
        if not self.flow.request or self.flow.request.get('idempotency_key')!=task.get('operation_key'):
            self.integrity_blocked=True
            raise NeedsInput('原操作编号发生变化，请先恢复原操作，不能继续推进计划。')
        self.integrity_blocked=False
        result=self.flow.result
        if result and result['status'] in {'succeeded','scheduled'}:
            self.receipts[task['id']]=deepcopy(result)
            task['status']='completed';self.waiting=None
        elif result and result['status'] in {'failed','blocked','cancelled'}:
            self.receipts[task['id']]=deepcopy(result)
            task['status']='stopped';self.waiting=None;self.paused=True

    def advance(self,*,allow_write=False):
        self._sync()
        if self.paused or self.waiting:return None
        if self.flow.blocked:raise NeedsInput('请先处理或核实原操作。')
        for task in self.tasks:
            if task['status']=='completed':continue
            if any(d not in self.receipts or self.receipts[d]['status'] not in {'succeeded','scheduled'} for d in task['depends_on']):return None
            ancestors=set(task['depends_on'])
            for _ in self.tasks:
                ancestors|={d for t in self.tasks if t['id'] in ancestors for d in t['depends_on']}
            action,params=compile_task(self.flow,task['intent'],task.get('fields',{}),selected_payee=self.choices.get(task['id']),
                results={key:value for key,value in self.receipts.items() if key in ancestors})
            if action=='__holdings__':
                result={'status':'succeeded','data':params,'source':'trusted_context'}
                self.receipts[task['id']]=result;task['status']='completed'
                return result
            # Green pure queries execute automatically; other actions keep explicit preparation + original authorization.
            if action not in {'account.balance','bill.analyze','wealth.compare','scenario.plan'} and not allow_write:return None
            try:
                result=self.flow.start_action(action,params)
            except (ContractError,ServiceError):
                raise NeedsInput('请求未通过银行校验，请核对参数和原操作。') from None
            self.waiting=task['id'];task['status']='awaiting_bank';task['operation_key']=self.flow.request['idempotency_key'];task['bank_action']=action
            self._sync()
            return result
        return None
