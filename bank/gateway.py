"""持久化模拟网关；身份由可信调用方提供，演示凭证不代表银行认证。"""

import sqlite3
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from uuid import uuid4
from card import TARGETS, checked_card

from shared import (ContractError, ServiceError, adapt_result, validate_request,
                    validate_authorization, operation_digest, required_gate, validate_transition)
from shared.adapters import loads_request, loads_result
from shared.contracts import MAX_SAFE_INTEGER
from .services import SandboxServices, require_principal, owned_account
from .store import utc_timestamp
from .transfer import handle, split_shares

PENDING = ('awaiting_confirmation', 'awaiting_verification')


class BankGateway:
    demo_mode = True
    write_actions = frozenset({'transfer.create', 'transfer.split', *TARGETS})

    def __init__(self, store, *, clock=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.services = SandboxServices(store, clock=self.clock)

    def get_context(self, *, principal_id):
        try:
            return self.services.get_context(principal_id=principal_id)
        except (sqlite3.Error, ContractError) as exc:
            raise ServiceError("UPSTREAM_ERROR", "模拟资源暂不可用，请检查数据。") from exc

    def list_operations(self, *, principal_id):
        """可信页面的恢复索引，不是模型动作；保留所有未决操作及最近 20 个终态。"""
        try:
            with self.store.connection() as c:
                require_principal(c, principal_id)
                unresolved, recent = [], []
                for row in c.execute('SELECT request_json FROM operations WHERE principal_id=? ORDER BY rowid DESC', (principal_id,)):
                    request, result = self._load(c, loads_request(row[0]), principal_id)
                    item = dict(action=request['action'], idempotency_key=request['idempotency_key'], status=result['status'])
                    if result['status'] in (*PENDING, 'running', 'unknown'):
                        unresolved.append(item)
                    elif len(recent) < 20:
                        recent.append(item)
            return unresolved + recent
        except (sqlite3.Error, ContractError) as exc:
            raise ServiceError('UPSTREAM_ERROR', '暂时无法读取待处理操作。') from exc

    def operation_payee(self, request, *, principal_id):
        """恢复详情的可信展示信息；映射已变时不把新账户冒充原确认对象。"""
        with self.store.connection() as c:
            require_principal(c, principal_id)
            saved = self._load(c, request, principal_id)
            if saved is None or request['action'] != 'transfer.create':
                return None
            binding = c.execute('''SELECT payee_account_id FROM challenges
                WHERE principal_id=? AND action=? AND idempotency_key=? ORDER BY rowid DESC LIMIT 1''',
                self._key(request, principal_id)).fetchone()
            row = c.execute('SELECT * FROM payees WHERE principal_id=? AND payee_id=?',
                (principal_id, request['params']['payee_id'])).fetchone()
            if not binding or not row or binding[0] != row['account_id']:
                return None
            return {k: row[k] for k in ('payee_id', 'display_name', 'masked_account', 'bank_name') if row[k] is not None}

    def execute(self, request, *, principal_id):
        try:
            validate_request(request)
        except ContractError as exc:
            raise ServiceError("INVALID_REQUEST", "请求格式不符合 v1.1。") from exc
        request = deepcopy(request)
        if request['action'] in self.write_actions:
            return self._execute_write(request, principal_id)
        try:
            data = self.services.query(request["action"], request["params"], principal_id=principal_id)
            return adapt_result(request, {"status": "succeeded", "data": data}, source="sandbox")
        except ServiceError as exc:
            code = exc.code
        except (sqlite3.Error, ContractError):
            code = "UPSTREAM_ERROR"
        return adapt_result(request, {"status": "failed", "error": {
            "code": code, "message": "查询或操作未完成。"}}, source="sandbox")

    def authorize(self, payload, *, principal_id):
        try:
            validate_authorization(payload)
        except ContractError as exc:
            raise ServiceError("INVALID_REQUEST", "授权格式无效。") from exc
        payload = deepcopy(payload)
        error = None
        try:
            with self.store.connection(write=True) as c:
                row, request, previous = self._challenge(c, payload['challenge_id'], principal_id)
                now = self.clock()
                if not hmac.compare_digest(row['digest'], payload['operation_digest']):
                    self._audit(c, request, principal_id, 'authorization_invalid', now, code='AUTH_INVALID')
                    error = ServiceError('AUTH_INVALID', '操作摘要不匹配。')
                elif previous['status'] not in PENDING:
                    result = previous
                elif payload['decision'] == 'reject':
                    result = self._finish(c, row, request, previous, principal_id, now, 'cancelled')
                elif self._expired(row, now):
                    result = self._finish(c, row, request, previous, principal_id, now, 'expired', 'AUTH_EXPIRED')
                else:
                    result, error = self._approve(c, row, request, previous, principal_id, payload, now)
            if error:
                raise error  # 先提交失败计数和审计，避免随异常回滚。
            return result
        except (sqlite3.Error, ContractError) as exc:
            # 提交结果不确定时不另写“失败”，由调用方查询原操作。
            raise ServiceError('RESULT_UNKNOWN', '执行结果未知，请查询原操作。') from exc
        except ServiceError as exc:
            if error is None and exc.code in ('AUTH_INVALID', 'FORBIDDEN'):
                self._audit_rejected_identity(principal_id, payload['challenge_id'])
            raise

    def _audit_rejected_identity(self, principal, challenge_id):
        # 不读取/记录他人的原请求，也不增加他人的验证失败次数。
        try:
            with self.store.connection(write=True) as c:
                require_principal(c, principal)
                c.execute('''INSERT INTO audit_events
                    (principal_id,trace_id,action,outcome,occurred_at,details_json) VALUES (?,?,?,?,?,?)''',
                    (principal, 'auth_'+uuid4().hex, 'authorization', 'authorization_invalid',
                     utc_timestamp(self.clock().isoformat()), json.dumps(dict(challenge_id=challenge_id, code='AUTH_INVALID'))))
        except (sqlite3.Error, ServiceError):
            pass  # 无可信身份或审计库不可用时仍拒绝授权，不修改任何业务记录。

    @staticmethod
    def _key(request, principal):
        return principal, request['action'], request['idempotency_key']

    def _load(self, c, request, principal):
        row = c.execute('SELECT * FROM operations WHERE principal_id=? AND action=? AND idempotency_key=?',
                        self._key(request, principal)).fetchone()
        if row is None:
            return None
        if row['digest'] != operation_digest(request, principal_id=principal):
            raise ServiceError('IDEMPOTENCY_CONFLICT', '同一操作键的内容已改变。')
        original = loads_request(row['request_json'])
        result = loads_result(original, row['result_json'])
        if (self._key(original, principal) != self._key(request, principal)
                or operation_digest(original, principal_id=principal) != row['digest']
                or result['source'] not in ('gateway', 'sandbox')):
            raise ServiceError('INVALID_STATE', '原操作记录不一致。')
        if result['status'] in PENDING and result['next_action']['operation_digest'] != row['digest']:
            raise ServiceError('INVALID_STATE', '原挑战摘要不一致。')
        return original, result

    @staticmethod
    def _replay(request, result):
        return adapt_result(request, {k: v for k, v in result.items()
            if k in ('status', 'data', 'error', 'next_action')}, source=result['source'])

    def _save(self, c, request, result, principal, previous=None):
        if previous is not None:
            validate_transition(request, previous, result)
        c.execute('''INSERT INTO operations VALUES (?,?,?,?,?,?)
            ON CONFLICT(principal_id,action,idempotency_key) DO UPDATE SET result_json=excluded.result_json''',
            (*self._key(request, principal), operation_digest(request, principal_id=principal),
             json.dumps(request), json.dumps(result)))

    @staticmethod
    def _audit(c, request, principal, outcome, now, **details):
        c.execute('''INSERT INTO audit_events
            (principal_id,trace_id,action,outcome,occurred_at,details_json) VALUES (?,?,?,?,?,?)''',
            (principal, request['trace_id'], request['action'], outcome, utc_timestamp(now.isoformat()),
             json.dumps(dict(idempotency_key=request['idempotency_key'], **details))))

    @staticmethod
    def _payee(c, request, principal):
        p = request['params']
        owned_account(c, p['account_id'], principal)
        row = c.execute('SELECT account_id FROM payees WHERE principal_id=? AND payee_id=?',
                        (principal, p['payee_id'])).fetchone()
        if row is None:
            raise ServiceError('FORBIDDEN', '收款人不属于当前用户。')
        if row[0] == p['account_id']:
            raise ServiceError('INVALID_REQUEST', '付款与收款账户不能相同。')
        return row[0]

    @staticmethod
    def _kind(c, request, principal, now):
        if request['action'] != 'transfer.create':
            return required_gate(request)['required_action']
        local = now.astimezone(timezone(timedelta(hours=8)))
        lower = utc_timestamp(datetime.combine(local.date(), time.min, local.tzinfo).isoformat())
        upper = utc_timestamp(datetime.combine(local.date(), time.max, local.tzinfo).isoformat())
        used = sum(row[0] for row in c.execute(
            'SELECT amount_minor FROM transfers WHERE principal_id=? AND occurred_at BETWEEN ? AND ?',
            (principal, lower, upper)))
        if used + request['params']['amount_minor'] > MAX_SAFE_INTEGER:
            raise ServiceError('LIMIT_EXCEEDED', '日累计金额超出范围。')
        return required_gate(request, transfer_used_minor=used)['required_action']

    def _pending(self, c, request, principal, kind, payee, now, previous=None):
        challenge = 'challenge_' + uuid4().hex
        digest = operation_digest(request, principal_id=principal)
        expires = utc_timestamp((now + timedelta(minutes=10)).isoformat())
        next_action = dict(challenge_id=challenge, operation_digest=digest, kind=kind, expires_at=expires)
        if kind == 'verify':
            next_action['methods'] = ['demo_otp']
        status = 'awaiting_verification' if kind == 'verify' else 'awaiting_confirmation'
        result = adapt_result(request, dict(status=status, next_action=next_action), source='gateway')
        self._save(c, request, result, principal, previous)
        c.execute('''INSERT INTO challenges
            (challenge_id,principal_id,action,idempotency_key,digest,kind,expires_at,state,payee_account_id,resource_binding_json)
            VALUES (?,?,?,?,?,?,?,'pending',?,?)''',
            (challenge, *self._key(request, principal), digest, kind, expires,
             payee if request['action'] == 'transfer.create' else None, json.dumps(payee)))
        self._audit(c, request, principal, 'challenge_created', now, challenge_id=challenge, kind=kind)
        return result

    @staticmethod
    def _error(request, code, *, status='failed'):
        messages = {'INSUFFICIENT_FUNDS': '可用余额不足，本次转账未执行。',
            'AUTH_EXPIRED': '授权已过期，本次操作未执行；仍有需要时请重新发起。',
            'AUTH_INVALID': '授权或操作资源已失效，本次操作未执行，请重新核对。',
            'SAFETY_LOCKED': '模拟验证连续五次失败，本次操作已锁定，未执行。',
            'IDEMPOTENCY_CONFLICT': '原操作编号对应的内容不同，请核实原操作，不要重复提交。',
            'FORBIDDEN': '无权使用此账户、卡片或联系人，本次操作未执行。',
            'INVALID_STATE': '当前状态不允许此操作，请刷新后核对。',
            'RESULT_UNKNOWN': '结果待核实，请查询原操作，不要重复发起。'}
        return adapt_result(request, dict(status=status, error=dict(code=code,
            message=messages.get(code, '操作未完成，请查询原操作并检查模拟银行状态。'))), source='gateway')

    def _execute_write(self, request, principal):
        try:
            # ponytail: SQLite 串行写事务适合本地演示；多实例高吞吐时再更换存储。
            with self.store.connection(write=True) as c:
                require_principal(c, principal)
                saved = self._load(c, request, principal)
                if saved:
                    result = self._replay(request, saved[1])
                else:
                    now = self.clock()
                    try:
                        payee = self._binding(c, request, principal)
                        kind = self._kind(c, request, principal, now)
                    except ServiceError as exc:
                        result = self._error(request, exc.code)
                        self._save(c, request, result, principal)
                        self._audit(c, request, principal, 'failed', now, code=exc.code)
                    else:
                        result = self._pending(c, request, principal, kind, payee, now)
            return result  # 外层事务提交后才返回。
        except ServiceError as exc:
            return self._error(request, exc.code)
        except (sqlite3.Error, ContractError):
            return self._error(request, 'RESULT_UNKNOWN', status='unknown')

    def _challenge(self, c, challenge_id, principal):
        require_principal(c, principal)
        row = c.execute('SELECT * FROM challenges WHERE challenge_id=? AND principal_id=?',
                        (challenge_id, principal)).fetchone()
        if row is None:
            raise ServiceError('AUTH_INVALID', '授权凭据无效或不属于当前用户。')
        op = c.execute('SELECT request_json FROM operations WHERE principal_id=? AND action=? AND idempotency_key=?',
                        (principal, row['action'], row['idempotency_key'])).fetchone()
        if op is None:
            raise ServiceError('INVALID_STATE', '原操作不存在。')
        request, result = self._load(c, loads_request(op[0]), principal)
        if row['digest'] != operation_digest(request, principal_id=principal):
            raise ServiceError('INVALID_STATE', '挑战与原操作不一致。')
        if result['status'] in PENDING and (result['next_action']['challenge_id'] != challenge_id
                or result['next_action']['kind'] != row['kind']
                or result['next_action']['expires_at'] != row['expires_at']
                or row['state'] != 'pending'):
            raise ServiceError('AUTH_INVALID', '挑战已被替换或状态不一致。')
        return row, request, result

    @staticmethod
    def _expired(row, now):
        return now >= datetime.fromisoformat(row['expires_at'].replace('Z', '+00:00'))

    def _finish(self, c, row, request, previous, principal, now, state, code=None):
        status = {'cancelled': 'cancelled', 'expired': 'failed', 'blocked': 'blocked', 'used': 'failed'}[state]
        result = self._error(request, code, status=status) if code else adapt_result(
            request, dict(status=status), source='gateway')
        c.execute('UPDATE challenges SET state=?,proof_digest=NULL WHERE challenge_id=?', (state, row['challenge_id']))
        self._save(c, request, result, principal, previous)
        self._audit(c, request, principal, status, now, code=code, challenge_id=row['challenge_id'])
        return result

    def issue_demo_proof(self, challenge_id, *, principal_id):
        """仅可信演示服务调用，不注册为模型工具；不保存明文、不重置失败次数。"""
        error = None
        try:
            with self.store.connection(write=True) as c:
                row, request, previous = self._challenge(c, challenge_id, principal_id)
                now = self.clock()
                if row['kind'] != 'verify' or row['state'] != 'pending' or previous['status'] not in PENDING:
                    raise ServiceError('AUTH_INVALID', '当前没有待验证挑战。')
                if self._expired(row, now):
                    self._finish(c, row, request, previous, principal_id, now, 'expired', 'AUTH_EXPIRED')
                    error = ServiceError('AUTH_EXPIRED', '授权已过期，请重新发起操作。')
                else:
                    token = secrets.token_urlsafe(32)
                    c.execute('UPDATE challenges SET proof_digest=? WHERE challenge_id=?',
                        (hashlib.sha256(token.encode()).hexdigest(), challenge_id))
                    self._audit(c, request, principal_id, 'demo_proof_issued', now, challenge_id=challenge_id)
        except (sqlite3.Error, ContractError) as exc:
            raise ServiceError('UPSTREAM_ERROR', '演示凭据暂不可用。') from exc
        if error:
            raise error
        return token

    def _approve(self, c, row, request, previous, principal, payload, now):
        try:
            payee = self._binding(c, request, principal)
            # 老转账挑战继续使用原绑定字段；新业务不得把空绑定当作有效授权。
            bound = row['payee_account_id'] if request['action'] == 'transfer.create' else row['resource_binding_json']
            expected = payee if request['action'] == 'transfer.create' else json.dumps(payee)
            if bound != expected:
                raise ServiceError('AUTH_INVALID', '操作资源已变化，必须重新发起确认。')
            kind = self._kind(c, request, principal, now)
        except ServiceError as exc:
            return self._finish(c, row, request, previous, principal, now, 'used', exc.code), None
        if row['kind'] == 'confirm' and kind == 'verify':
            c.execute("UPDATE challenges SET state='cancelled',proof_digest=NULL WHERE challenge_id=?", (row['challenge_id'],))
            return self._pending(c, request, principal, 'verify', payee, now, previous), None
        if row['kind'] == 'verify':
            digest = hashlib.sha256(payload.get('proof_token', '').encode()).hexdigest()
            if not row['proof_digest'] or not hmac.compare_digest(row['proof_digest'], digest):
                attempts = row['failed_attempts'] + 1
                c.execute('UPDATE challenges SET failed_attempts=? WHERE challenge_id=?', (attempts, row['challenge_id']))
                self._audit(c, request, principal, 'verification_invalid', now, attempts=attempts, challenge_id=row['challenge_id'])
                if attempts >= 5:
                    return self._finish(c, row, request, previous, principal, now, 'blocked', 'SAFETY_LOCKED'), None
                return previous, ServiceError('AUTH_INVALID', '模拟验证失败；连续五次失败锁定本次操作。')
        try:
            with self.store.savepoint(c):
                result = self._commit_action(c, request, previous, principal, now)
                if self._load(c, request, principal)[1] != result:
                    raise ServiceError('UPSTREAM_ERROR', '模块回执与已保存结果不一致。')
                c.execute("UPDATE challenges SET state='used',proof_digest=NULL WHERE challenge_id=?", (row['challenge_id'],))
                self._audit(c, request, principal, 'authorization_consumed', now, challenge_id=row['challenge_id'])
        except (ServiceError, sqlite3.Error, ContractError) as exc:
            # 保存点已回滚资金、业务记录和挑战消费后，才可记录失败。
            code = exc.code if isinstance(exc, ServiceError) else 'UPSTREAM_ERROR'
            return self._finish(c, row, request, previous, principal, now, 'used', code), None
        return result, None

    def _binding(self, c, request, principal):
        if request['action'] == 'transfer.create':
            return self._payee(c, request, principal)
        if request['action'] in TARGETS:
            row = checked_card(c, request['params']['card_id'], principal, request['action'])
            return [row['card_id'], row['account_id'], row['card_state'], row['revision']]
        if request['action'] == 'transfer.split':
            p = request['params']
            owned_account(c, p['account_id'], principal)
            people = []
            for payee in p['payee_ids']:
                row = c.execute('SELECT account_id FROM payees WHERE principal_id=? AND payee_id=?',
                                (principal, payee)).fetchone()
                if row is None:
                    raise ServiceError('FORBIDDEN', '参与人不属于当前用户的联系人。')
                people.append([payee, row[0]])
            return [p['account_id'], people]
        raise ServiceError('INVALID_REQUEST', '该业务尚未实现。')

    def _commit_action(self, c, request, previous, principal, now):
        """仅在同一写事务完成授权后调用；失败由外层保存点整体回滚。"""
        if request['action'] == 'transfer.split':
            shares = split_shares(request)
            collection_id = 'collection_' + uuid4().hex
            c.execute('INSERT INTO collections VALUES (?,?,?,?,?,?,?)',
                (collection_id, principal, request['params']['account_id'], request['idempotency_key'],
                 request['params']['amount_minor'], json.dumps(shares), utc_timestamp(now.isoformat())))
            result = adapt_result(request, dict(status='succeeded',
                data=dict(collection_id=collection_id, shares=shares)), source='sandbox')
            self._save(c, request, result, principal, previous)
            return result
        if request['action'] in TARGETS:
            card_id, target = request['params']['card_id'], TARGETS[request['action']]
            c.execute('UPDATE cards SET card_state=?,revision=revision+1 WHERE card_id=?', (target, card_id))
            result = adapt_result(request, dict(status='succeeded', data=dict(card_id=card_id, card_state=target)), source='sandbox')
            self._save(c, request, result, principal, previous)
            return result
        if request['action'] != 'transfer.create':
            raise ServiceError('INVALID_REQUEST', '该业务尚未实现。')
        with self.services.transfer_context(c, request, principal_id=principal, now=now) as context:
            raw = handle(context['request']['params'], context=context)
        return adapt_result(request, raw, source='sandbox')
