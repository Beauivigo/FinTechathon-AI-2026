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


def money_minor(value,*,allow_zero=False):
    """Trusted yuan text to integer minor units; never guessed or defaulted."""
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?元?',value.strip()):
        raise NeedsInput('请给出准确的正数金额，最多两位小数。')
    amount=yuan_to_minor(value.strip().removesuffix('元'))
    if amount<0 or (amount==0 and not allow_zero):raise NeedsInput('金额必须大于零。')
    return amount


_money=money_minor


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
        memo=p.get('memo')
        if memo not in (None,''):
            if not isinstance(memo,str) or not memo.strip() or len(memo)>200:raise NeedsInput('转账备注须为1—200个字符，且不能为空。')
            params['memo']=memo.strip()
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
        if any(p.get(k) for k in ('budget_text','item_ids','delivery_address_id','budget_at','purchase_at')):
            # A full birthday schedule is expanded by the trusted birthday chain;
            # this path must never pretend the read-only date plan bought anything.
            raise NeedsInput('完整生日定时计划由生日计划入口登记后逐步执行；当前只可只读核对生日日期。')
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


TRUSTED_TASK_KEYS=('not_before','operation_key','request','registration_request','registration_result',
    'execution_state','bank_action')
GREEN_PLAN_ACTIONS=frozenset({'account.balance','bill.analyze','wealth.compare','scenario.plan'})


class BankPlanRunner:
    """模型不接触实例；每步使用原Conversation的确认、强验证及恢复。"""
    def __init__(self,flow,tasks,*,trace_id=None,audit=None,journal=None):
        self.flow=flow
        if trace_id is not None:
            from .model_safety import check_trace
            check_trace(trace_id)
        self.trace_id=trace_id;self.audit=audit
        self.tasks=deepcopy(tasks)
        self.receipts={}
        self.paused=False
        self.waiting=None
        self.choices={}
        self.integrity_blocked=False
        self.journal=journal
        self.revision=0
        self.automation=False
        for t in self.tasks:
            t['status']='pending'
        self._validate_graph()

    def checkpoint(self,event='progress'):
        if self.journal:
            try:self.journal.save(self,event)
            except Exception:
                self.integrity_blocked=True;self.paused=True
                raise NeedsInput('计划记录未能保存或已有新版本，请恢复原计划，不能继续执行。') from None

    @classmethod
    def restore(cls,flow,journal,plan_id,*,audit=None):
        snapshot,revision=journal.load(plan_id,flow.principal_id)
        runner=cls(flow,snapshot['tasks'],trace_id=plan_id,audit=audit,journal=journal)
        runner.tasks=deepcopy(snapshot['tasks']);runner.receipts=deepcopy(snapshot['receipts'])
        runner.waiting=snapshot['waiting'];runner.paused=snapshot['paused'];runner.choices=snapshot['choices'];runner.revision=revision
        runner.automation=snapshot.get('automation',False)
        # Query original before replaying exactly the same saved key. Never infer unpaid from NOT_FOUND.
        if runner.waiting:
            task=next(t for t in runner.tasks if t['id']==runner.waiting)
            request=task['request']
            flow.request=deepcopy(request);flow.result=None;flow.backend_result=None
            flow.result=flow._uncertain()
            if request['action'] in flow.gateway.write_actions or request['action']=='subscription.list':
                flow.recover(action=request['action'],idempotency_key=request['idempotency_key'])
            if flow.result is None or flow.needs_reconciliation:
                flow.result=flow._send_write(flow.gateway.execute,request)
            runner._sync()
        return runner

    def inherit_completed(self, previous):
        """Only bank-verified completed steps may survive a plan revision.

        Trusted constraints of unfinished steps are never dropped by a model
        revision: a matching task id keeps its ``not_before`` gate and any
        prepared operation key, an explicit new value from a trusted caller is
        respected, and an unmatched constrained or prepared step rejects the
        revision so the stored plan stays intact.
        """
        if previous.waiting or previous.integrity_blocked:
            raise NeedsInput('原步骤尚待核实，不能修改计划。')
        current = {t['id']: t for t in self.tasks}
        inherited = set()
        for old in previous.tasks:
            if old['status'] != 'completed':
                continue
            new = current.get(old['id'])
            if not new or any(new.get(k) != old.get(k) for k in ('intent','fields','depends_on')):
                receipt = previous.receipts.get(old['id'])
                if (old.get('bank_action') in GREEN_PLAN_ACTIONS and old['id'] not in current
                        and receipt is not None and receipt.get('status') == 'succeeded'):
                    # A completed read-only projection has no funds effect and may
                    # be replaced by a new trusted plan; plan_events keeps the history.
                    continue
                raise NeedsInput('已完成步骤不能删除、改编号或修改内容；请保留原步骤并人工核对。')
            if old['id'] not in previous.receipts:
                raise NeedsInput('已完成步骤缺少可信回执，请查询原操作。')
            inherited.add(old['id'])
        for old in previous.tasks:
            if old['status'] == 'completed':
                if old['id'] not in inherited:
                    continue
                current[old['id']].update(deepcopy(old))
                self.receipts[old['id']] = deepcopy(previous.receipts[old['id']])
        for old in previous.tasks:
            if old['status'] == 'completed':
                continue
            new = current.get(old['id'])
            prepared = bool(old.get('request') or old.get('operation_key') or old.get('registration_request'))
            constrained = ('not_before' in old or 'registration_result' in old or 'execution_state' in old)
            if new is None:
                if prepared or constrained:
                    raise NeedsInput('未完成步骤的时间或操作约束不能删除；请保留原编号，或先暂停/结束原计划。')
                continue
            if prepared and any(new.get(k) != old.get(k) for k in ('intent','fields','depends_on')):
                raise NeedsInput('该步骤已有准备中的银行操作编号，不能改内容；请保留原步骤或先核实原操作。')
            for key in TRUSTED_TASK_KEYS:
                if key in old and key not in new:
                    new[key] = deepcopy(old[key])
        self.choices = deepcopy(previous.choices)
        self.revision = previous.revision
        self.automation = previous.automation

    def _audit_result(self,task):
        if not self.audit:return
        try:
            self.audit(dict(trace_id=self.trace_id,event='step_result',version=0,failures=0,
                phase='plan',intent=task['intent']))
        except Exception:
            self.integrity_blocked=True;self.paused=True
            raise NeedsInput('步骤审计未能保存，已停止计划；已提交操作请查询原结果。') from None

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
        if self.journal and result and result['status']=='scheduled':
            import json
            with self.flow.gateway.store.connection() as c:
                row=c.execute('SELECT spec_json,state FROM tasks WHERE task_id=? AND principal_id=?',
                    (result['data']['schedule_id'],self.flow.principal_id)).fetchone()
                if row is None:raise NeedsInput('定时登记缺少可信任务，请核实。')
                actual=json.loads(row['spec_json'])['payload']
                saved=c.execute('SELECT 1 FROM operations WHERE principal_id=? AND action=? AND idempotency_key=?',
                    (self.flow.principal_id,actual['action'],actual['idempotency_key'])).fetchone()
            task['registration_result']=deepcopy(result)
            if not saved:
                if row['state'] in ('failed','cancelled'):
                    task['status']='stopped';task['execution_state']=row['state'];self.paused=True;self.waiting=None
                self.checkpoint();return
            task['registration_request']=task['request'];task['request']=actual
            task['operation_key']=actual['idempotency_key'];task['bank_action']=actual['action']
            self.flow.recover(action=actual['action'],idempotency_key=actual['idempotency_key'])
            self.checkpoint();self._sync();return
        if result and result['status'] in {'succeeded','scheduled'}:
            self._audit_result(task)
            self.receipts[task['id']]=deepcopy(result)
            task['status']='completed';self.waiting=None
        elif result and result['status'] in {'failed','blocked','cancelled'}:
            self._audit_result(task)
            self.receipts[task['id']]=deepcopy(result)
            task['status']='stopped';self.waiting=None;self.paused=True
        self.checkpoint()

    def advance(self,*,allow_write=False):
        self._sync()
        if self.paused or self.waiting:return None
        blocked=self.flow.blocked
        for task in self.tasks:
            if task['status']=='completed':continue
            if task.get('not_before'):
                from datetime import datetime
                if self.flow.gateway.clock()<datetime.fromisoformat(task['not_before']):return None
            if any(d not in self.receipts or self.receipts[d]['status'] not in {'succeeded','scheduled'} for d in task['depends_on']):return None
            ancestors=set(task['depends_on'])
            for _ in self.tasks:
                ancestors|={d for t in self.tasks if t['id'] in ancestors for d in t['depends_on']}
            action,params=compile_task(self.flow,task['intent'],task.get('fields',{}),selected_payee=self.choices.get(task['id']),
                results={key:value for key,value in self.receipts.items() if key in ancestors})
            if blocked and action not in GREEN_PLAN_ACTIONS | {'__holdings__'}:
                raise NeedsInput('请先处理或核实原操作，再提交这项业务；纯查询仍可继续。')
            if action=='__holdings__':
                result={'status':'succeeded','data':params,'source':'trusted_context'}
                self._audit_result(task)
                self.receipts[task['id']]=result;task['status']='completed'
                self.checkpoint()
                return result
            if blocked:
                # A pure observation must never replace the original pending or
                # unknown request held by Conversation, even when it fails.
                request=task.get('request') or adapt_request(action,params,
                    request_id='req_'+uuid4().hex,trace_id=self.trace_id,
                    idempotency_key='operation_'+uuid4().hex)
                if not getattr(self.flow.gateway,'demo_mode',True):request['schema_version']='1.2'
                if request['action']!=action or request['params']!=params:
                    raise NeedsInput('查询草稿已变化，请重新核对；原操作保持不变。')
                task.update(request=request,operation_key=request['idempotency_key'],bank_action=action)
                result=validate_result(request,self.flow.gateway.execute(request,principal_id=self.flow.principal_id))
                self._audit_result(task)
                self.receipts[task['id']]=deepcopy(result)
                task['status']='completed' if result['status']=='succeeded' else 'stopped'
                if task['status']=='stopped':self.paused=True
                self.checkpoint()
                return result
            # Green pure queries execute automatically; other actions keep explicit preparation + original authorization.
            if action not in {'account.balance','bill.analyze','wealth.compare','scenario.plan'} and not allow_write:return None
            try:
                if self.journal:
                    request=task.get('request') or adapt_request(action,params,request_id='req_'+uuid4().hex,
                        trace_id=self.trace_id,idempotency_key='operation_'+uuid4().hex)
                    if not getattr(self.flow.gateway,'demo_mode',True):request['schema_version']='1.2'
                    task['request']=request;task['operation_key']=request['idempotency_key'];task['bank_action']=action
                    self.waiting=task['id'];task['status']='awaiting_bank'
                    self.checkpoint('prepared')
                    self.waiting=None
                    result=self.flow.start_action(action,params,plan_trace_id=self.trace_id,prepared_request=request)
                else:
                    result=self.flow.start_action(action,params,plan_trace_id=self.trace_id)
            except (ContractError,ServiceError):
                raise NeedsInput('请求未通过银行校验，请核对参数和原操作。') from None
            self.waiting=task['id'];task['status']='awaiting_bank';task['operation_key']=self.flow.request['idempotency_key'];task['bank_action']=action
            self._sync()
            return result
        return None
