"""A 的调用流程。演示网关与后续业务网关沿用同名入口。"""

from copy import deepcopy
from uuid import uuid4

from shared import (SCHEMA_VERSION, ServiceError, adapt_request, adapt_result, result_text, validate_context,
                    validate_result, validate_transition, operation_digest)
from .intent import parse_transfer

# 未决状态：必须由用户确认/强验证或经权威查询后才能继续。
UNRESOLVED = frozenset({'awaiting_confirmation', 'awaiting_verification', 'running', 'unknown'})


class Conversation:
    def __init__(self, gateway, *, principal_id):
        self.gateway, self.principal_id = gateway, principal_id
        self.resources = validate_context(gateway.get_context(principal_id=principal_id))
        self.messages, self.candidates = [], []
        self.draft, self.request, self.result = None, None, None
        self.payee = None
        self.trace_id = None
        self.backend_result = None
        self.needs_reconciliation = False
        self.external_blocked = False
        self.operations = []
        self.refresh_operations()

    def refresh_operations(self):
        if hasattr(self.gateway, 'list_operations'):
            self.operations = self.gateway.list_operations(principal_id=self.principal_id)
        return self.operations

    @property
    def unresolved(self):
        """来自权威操作索引的未决项；不清空、不猜测，只反映最近一次刷新。"""
        return tuple(op for op in self.operations if op['status'] in UNRESOLVED)

    def settle(self):
        """把本页快照对齐权威操作记录；只有权威终态才清除本地的未决标记。

        另一页面/会话已经确认、拒绝或使授权过期时，本地仍可能保留旧的待确认
        或未知快照。这里只通过原有 recover() 重新读取原操作（校验归属、摘要与
        合法状态迁移），不会把 NOT_FOUND 或索引缺失当成未执行，也不会直接改写
        结果或资金。
        """
        try:
            self.refresh_operations()
        except ServiceError:
            return self.blocked
        # 刷新/切页不代替用户主动核实；索引终态也不能自动消除本地unknown。
        if self.needs_reconciliation or bool(self.result and self.result['status'] in {'unknown','running'}):
            return self.blocked
        request = self.request
        if request is None:
            return self.blocked
        stale = self.pending
        if not stale:
            return self.blocked
        key = (request['action'], request.get('idempotency_key', request['request_id']))
        status = next((op['status'] for op in self.operations
                       if (op['action'], op['idempotency_key']) == key), None)
        if status is not None and status not in UNRESOLVED:
            try:
                self.recover(action=request['action'],
                             idempotency_key=request.get('idempotency_key', request['request_id']))
            except Exception:
                pass  # 恢复失败时保留原请求与未决状态，交由页面继续查询。
        return self.blocked

    @property
    def pending(self):
        return not self.needs_reconciliation and bool(self.result and self.result["status"] in {"awaiting_confirmation", "awaiting_verification"})

    @property
    def blocked(self):
        return (self.external_blocked or self.needs_reconciliation or self.pending
            or bool(self.result and self.result['status'] in {'running', 'unknown'})
            or bool(self.unresolved))

    def _send_write(self, function, payload):
        """发送后的异常可能已经产生业务结果，保留原请求并阻止换键重试。"""
        try:
            result = function(deepcopy(payload), principal_id=self.principal_id)
            if self.backend_result:
                validate_transition(self.request, self.backend_result, result)
            else:
                validate_result(self.request, result)
            self.backend_result = deepcopy(result)
            self.needs_reconciliation = False
            return result
        except Exception as exc:
            # 授权失败也可能只代表本次凭证无效，不能据异常推断业务已结束。
            return self._uncertain()

    def _uncertain(self):
        self.needs_reconciliation = True
        source = self.backend_result['source'] if self.backend_result else 'gateway'
        return adapt_result(self.request, {'status': 'unknown', 'error': {
            'code': 'RESULT_UNKNOWN', 'message': '回执待核实，请查询原操作；不要重新发起。'}}, source=source)

    def recover(self, *, action=None, idempotency_key=None):
        """查询权威业务状态；客户端观察到 unknown 不写回后端，也不用于状态迁移。"""
        if action is None and self.request:
            action, idempotency_key = self.request['action'], self.request['idempotency_key']
        if not action or not idempotency_key:
            raise ValueError('请选择需要恢复的原操作。')
        same = bool(self.request and (action, idempotency_key) == (self.request['action'], self.request['idempotency_key']))
        if self.request and (self.pending or self.needs_reconciliation or self.result['status'] in {'running', 'unknown'}) and not same:
            raise ValueError('先处理或核实当前操作，再切换其他操作。')
        try:
            if action == 'subscription.list':
                data = self.gateway.get_confirmed_read(idempotency_key,principal_id=self.principal_id)
            else:
                query = adapt_request('operation.get', dict(original_action=action, original_idempotency_key=idempotency_key),
                    request_id='query_'+uuid4().hex, trace_id='recovery_'+uuid4().hex)
                reply = validate_result(query, self.gateway.execute(query, principal_id=self.principal_id))
                if reply['status'] != 'succeeded':
                    raise ServiceError('RESULT_UNKNOWN', '尚未找到可靠结果。')
                data = reply['data']
            original, result = data['operation_request'], data['operation_result']
            if (original['action'], original['idempotency_key']) != (action, idempotency_key):
                raise ValueError('原操作键不匹配。')
            if same:
                if operation_digest(original, principal_id=self.principal_id) != operation_digest(self.request, principal_id=self.principal_id):
                    raise ValueError('原操作内容不匹配。')
                if self.backend_result:
                    previous = deepcopy(self.backend_result)
                    previous.update(request_id=original['request_id'], trace_id=original['trace_id'])
                    validate_transition(original, previous, result)
            validate_result(original, result)
        except Exception:
            if self.request:
                self.result = self._uncertain()
            else:
                self.needs_reconciliation = True
            return self._say('未能核实原操作，不能据此认定未扣款。请保留操作编号并继续查询。')
        self.request, self.result = deepcopy(original), deepcopy(result)
        self.backend_result = deepcopy(result)
        self.needs_reconciliation = False
        self.trace_id = original['trace_id']
        self.draft, self.candidates = None, []
        # 仅展示可信快照里的姓名；缺失时显示已保存的 ID，不猜测新收款对象。
        self.payee = dict(display_name=original['params'].get('payee_id', '原操作'), masked_account='请核对原操作记录')
        if hasattr(self.gateway, 'operation_payee'):
            try:
                self.payee = self.gateway.operation_payee(original, principal_id=self.principal_id) or self.payee
            except Exception:
                pass  # 保留原 ID；展示查询故障不能改写已核实的业务事实。
        self.refresh_operations()
        return self._say(result_text(self.request, self.result))

    def _say(self, text):
        self.messages.append({"role": "assistant", "content": text})
        return text

    def submit(self, text):
        self.messages.append({"role": "user", "content": text})
        if self.blocked:
            return self._say("当前操作尚未结束，请先处理或核实原操作；不要重复提交。")
        # 新输入不能沿用上一笔的成功状态，避免错误输入旁仍显示“当前操作成功”。
        self.request = self.result = None
        self.backend_result = None
        self.needs_reconciliation = False
        self.payee = None
        self.candidates = []
        try:
            parsed = parse_transfer(text, self.draft)
        except ValueError as exc:
            self.draft = None
            self.trace_id = None
            return self._say(str(exc))
        if self.draft is None or parsed["payee_query"] != self.draft["payee_query"]:
            self.trace_id = "trace_" + uuid4().hex
            self.candidates = []
        self.draft = parsed
        if parsed["amount_yuan"] is None:
            return self._say(f"要给{parsed['payee_query']}转多少元？例如输入200元。")
        query = adapt_request("payee.resolve", {"query": parsed["payee_query"], "match_type": "name"},
                              request_id="req_" + uuid4().hex, trace_id=self.trace_id)
        result = validate_result(query, self.gateway.execute(query, principal_id=self.principal_id))
        if result["status"] != "succeeded":
            return self._say(result_text(query, result))
        self.candidates = deepcopy(result["data"]["candidates"])
        if not self.candidates:
            return self._say("没有找到该收款人，请核对姓名。本演示可使用小王或李明。")
        if len(self.candidates) > 1:
            return self._say("找到多位同名收款人，请根据银行和账户尾号选择。")
        return self.select_payee(self.candidates[0]["payee_id"])

    def select_payee(self, payee_id):
        if self.blocked:
            return self._say("当前操作尚未结束，请先处理或核实该操作。")
        choices = [row for row in self.candidates if row["payee_id"] == payee_id]
        if not self.draft or not self.draft["amount_yuan"] or len(choices) != 1:
            raise ValueError("请选择当前查询返回的收款人。")
        accounts = self.resources["accounts"]
        if len(accounts) != 1:
            raise ValueError("本轮演示需要一个明确的付款账户；多账户接入时须先增加用户选择。")
        self.payee = deepcopy(choices[0])
        self.request = adapt_request("transfer.create", {"account_id": accounts[0]["account_id"],
            "payee_id": payee_id, "amount_yuan": self.draft["amount_yuan"]},
            request_id="req_" + uuid4().hex, trace_id=self.trace_id,
            idempotency_key="operation_" + uuid4().hex)
        if not getattr(self.gateway,'demo_mode',True):self.request['schema_version']='1.2'
        self.result = self._send_write(self.gateway.execute, self.request)
        self.candidates, self.draft = [], None
        return self._say(result_text(self.request, self.result))

    def decide(self, approve, *, simulate_verification=False, totp_code=None):
        if not self.pending:
            return self.result
        challenge = self.result["next_action"]
        payload = {"schema_version": self.request["schema_version"], "challenge_id": challenge["challenge_id"],
                   "operation_digest": challenge["operation_digest"], "decision": "approve" if approve else "reject"}
        if approve and challenge["kind"] == "verify":
            if not getattr(self.gateway,'demo_mode',False):
                payload['proof_token']=self.gateway.issue_totp_proof(challenge['challenge_id'],totp_code,principal_id=self.principal_id)
            else:
                if not simulate_verification:raise ValueError('此操作需要验证；普通确认不能代替强验证。')
                try:
                    payload['proof_token']=self.gateway.issue_demo_proof(challenge['challenge_id'],principal_id=self.principal_id)
                except Exception:
                    self.result=self._uncertain();self._say('模拟验证未完成，请查询原操作状态。');return self.result
        result = self._send_write(self.gateway.authorize, payload)
        self.result = result
        try:
            self.refresh_operations()
        except ServiceError:
            # 已验证的交易回执不因历史索引读取失败而改写。
            self._say('暂未刷新操作列表，请稍后查询。')
        self._say(result_text(self.request, result))
        return result

    def start_action(self, action, params, *, plan_trace_id=None, prepared_request=None):
        """可信表单入口，共用原有发送、授权、结果核实与恢复流程。"""
        self.refresh_operations()
        if self.blocked:
            raise ValueError('请先处理或核实当前待办。')
        if plan_trace_id is not None:
            from .model_safety import check_trace
            check_trace(plan_trace_id)
        request = adapt_request(action, params, request_id='req_' + uuid4().hex,
            trace_id=plan_trace_id or 'trace_' + uuid4().hex, idempotency_key='operation_' + uuid4().hex)
        if prepared_request is not None:
            from shared import validate_request
            validate_request(prepared_request)
            if (prepared_request['action'] != action or prepared_request['params'] != params
                    or prepared_request['trace_id'] != plan_trace_id):
                raise ValueError('原计划请求与当前步骤不一致。')
            request = deepcopy(prepared_request)
        if not getattr(self.gateway,'demo_mode',True):request['schema_version']='1.2'
        self.request = request
        self.backend_result = None
        self.result = self._send_write(self.gateway.execute, request)
        self.refresh_operations()
        return self.result


def reconcile_session_flows(state, current):
    """Propagate unresolved client outcomes across all trusted page entry points."""
    flows = [state.get(key) for key in ('conversation', 'action_flow', 'model_bank_flow')]
    for other in flows:
        if other is None or other is current:
            continue
        # 兄弟页面对象可能保留已在他处收口的旧快照；按权威记录重新核对。
        if (getattr(other,'pending',False) and not other.needs_reconciliation
                and hasattr(other,'settle')):
            try:
                other.settle()
            except Exception:
                pass
    current.external_blocked = any(
        other is not None and other is not current and (
            other.needs_reconciliation or bool(other.result and other.result['status'] in {'unknown','running'})
        ) for other in flows
    )
    return current.external_blocked
