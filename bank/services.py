"""可信查询、转账资金和定时服务；预算预留/释放由网关调用budget模块。"""

from uuid import uuid4
from datetime import date, datetime, time, timedelta, timezone
from contextlib import closing, contextmanager
from copy import deepcopy
import json

from billing import analyze
from wealth import products
from shared import ServiceError, SCHEMA_VERSION, validate_context, operation_digest
from shared.adapters import loads_request, loads_result, strict_json_loads
from shared.contracts import ContractError, MAX_SAFE_INTEGER, check_value, validate_request
from shared.services import validate_funds_command, validate_funds_receipt
from .store import utc_timestamp


def require_principal(connection, principal_id):
    try:
        check_value(principal_id, "id", "principal_id")
    except ContractError as exc:
        raise ServiceError("FORBIDDEN", "模拟身份无效。") from exc
    if not connection.execute("SELECT 1 FROM principals WHERE principal_id=?", (principal_id,)).fetchone():
        raise ServiceError("FORBIDDEN", "模拟身份无效。")


def owned_account(connection, account_id, principal_id):
    row = connection.execute("SELECT * FROM accounts WHERE account_id=? AND principal_id=?",
                             (account_id, principal_id)).fetchone()
    if row is None:
        raise ServiceError("FORBIDDEN", "无权访问该账户。")
    return row


class SandboxServices:
    def __init__(self, store, *, clock=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._transfers = {}

    def register_task(self, transaction, spec, *, principal_id):
        from .scheduler import register
        return register(self,transaction,spec,principal_id)

    def get_task(self, task_id, *, principal_id):
        from .scheduler import owned_task, task_from
        with self.store.connection() as c:
            return task_from(owned_task(c,task_id,principal_id))

    def cancel_task(self, transaction, task_id, *, principal_id):
        from .scheduler import owned_task, task_from, transition
        self.store.require_write_transaction(transaction)
        row = owned_task(transaction,task_id,principal_id)
        if row['state']=='cancelled':
            return task_from(row)
        if row['state']!='scheduled':
            raise ServiceError('INVALID_STATE','任务已领取或结束，请查询原执行结果。')
        return transition(transaction,row,'cancelled',self.clock())

    @contextmanager
    def transfer_context(self, connection, request, *, principal_id, now=None):
        """仅供可信网关在核验授权后使用；P2 由内部测试代行该调用，绝非模型工具。"""
        self.store.require_write_transaction(connection)
        try:
            validate_request(request)
        except ContractError as exc:
            raise ServiceError("INVALID_REQUEST", "转账请求格式无效。") from exc
        if request["action"] != "transfer.create":
            raise ServiceError("INVALID_REQUEST", "此事务仅支持普通转账。")
        request = deepcopy(request)
        require_principal(connection, principal_id)
        token = object()
        with self.store.savepoint(connection):
            digest = operation_digest(request, principal_id=principal_id)
            saved = connection.execute("SELECT * FROM operations WHERE principal_id=? AND action=? AND idempotency_key=?",
                (principal_id, request["action"], request["idempotency_key"])).fetchone()
            previous, replay = None, None
            if saved:
                if saved["digest"] != digest:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "同一操作键的内容已改变。")
                original = loads_request(saved["request_json"])
                previous = loads_result(original, saved["result_json"])
                if (operation_digest(original, principal_id=principal_id) != digest
                        or original["action"] != saved["action"]
                        or original["idempotency_key"] != saved["idempotency_key"]
                        or previous["source"] not in ("gateway", "sandbox")):
                    raise ServiceError("UPSTREAM_ERROR", "原操作记录不一致。")
                request = original  # 保存的原请求不可被新的重试编号覆盖。
                if previous["status"] not in ("awaiting_confirmation", "awaiting_verification"):
                    replay = {k: deepcopy(v) for k, v in previous.items() if k in ("status", "data", "error")}
            state = dict(connection=connection, request=request, principal_id=principal_id,
                         digest=digest, previous=previous, replay=replay, completed=False)
            if replay is None:
                p = request["params"]
                owned_account(connection, p["account_id"], principal_id)
                payee = connection.execute("SELECT account_id FROM payees WHERE principal_id=? AND payee_id=?",
                                           (principal_id, p["payee_id"])).fetchone()
                if payee is None:
                    raise ServiceError("FORBIDDEN", "收款人不属于当前用户的可信映射。")
                if payee["account_id"] == p["account_id"]:
                    raise ServiceError("INVALID_REQUEST", "付款与收款账户不能相同。")
                reference = "transfer_" + uuid4().hex
                occurred_at = utc_timestamp((now or self.clock()).isoformat())
                state.update(reference=reference, occurred_at=occurred_at, commands={})
                for effect, account in (("debit", p["account_id"]), ("credit", payee["account_id"])):
                    state["commands"][effect] = dict(schema_version=SCHEMA_VERSION,
                        posting_key=f"{reference}_{effect}", trace_id=request["trace_id"],
                        account_id=account, reference_id=reference, effect=effect,
                        amount_minor=p["amount_minor"], kind="transfer")
            self._transfers[token] = state
            try:
                yield dict(principal_id=principal_id, request=deepcopy(request), services=self, transaction=token)
                if not state["completed"]:
                    raise ServiceError("INVALID_STATE", "转账未完整保存，回滚全部子动作。")
            finally:
                del self._transfers[token]

    def _transfer_state(self, transaction, principal_id):
        # 身份比较而非信任传入字典；过期、外部连接和其他服务实例的令牌均拒绝。
        state = next((v for k, v in self._transfers.items() if k is transaction), None)
        if state is None or state["principal_id"] != principal_id:
            raise ServiceError("FORBIDDEN", "无效的内部资金事务。")
        self.store.require_write_transaction(state["connection"])
        return state

    def post_funds(self, transaction, command, *, principal_id):
        state = self._transfer_state(transaction, principal_id)
        try:
            validate_funds_command(command)
        except ContractError as exc:
            raise ServiceError("INVALID_REQUEST", "资金命令格式无效。") from exc
        command = deepcopy(command)
        validate_funds_command(command)
        if command["effect"] not in ("debit", "credit"):
            raise ServiceError("INVALID_REQUEST", "转账资金上下文不允许预算动作。")
        expected = next((c for c in state.get("commands", {}).values()
                         if c["posting_key"] == command["posting_key"]), None)
        if expected is None:
            raise ServiceError("FORBIDDEN", "资金命令不属于本笔转账。")
        connection = state["connection"]
        with self.store.savepoint(connection):
            existing = connection.execute("SELECT * FROM fund_postings WHERE posting_key=?", (command["posting_key"],)).fetchone()
            if existing:
                if existing["principal_id"] != principal_id or strict_json_loads(existing["command_json"]) != command:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "子记账键对应的内容已改变。")
                if command != expected:
                    raise ServiceError("FORBIDDEN", "子记账与当前业务不符。")
                return validate_funds_receipt(command, strict_json_loads(existing["receipt_json"]))
            if command != expected:
                raise ServiceError("FORBIDDEN", "禁止任意扣款或充值。")
            debit = command["effect"] == "debit"
            if debit:
                account = owned_account(connection, command["account_id"], principal_id)
            else:
                debit_key = state["commands"]["debit"]["posting_key"]
                if not connection.execute("SELECT 1 FROM fund_postings WHERE posting_key=? AND principal_id=?",
                                          (debit_key, principal_id)).fetchone():
                    raise ServiceError("INVALID_STATE", "配对扣款尚未记账，禁止单独入账。")
                account = connection.execute("SELECT * FROM accounts WHERE account_id=?", (command["account_id"],)).fetchone()
                if account is None:
                    raise ServiceError("NOT_FOUND", "收款账户不存在。")
            amount = command["amount_minor"]
            if debit and account["available_minor"] < amount:
                raise ServiceError("INSUFFICIENT_FUNDS", "可用余额不足。")
            delta = -amount if debit else amount
            if not (0 <= account["balance_minor"] + delta <= MAX_SAFE_INTEGER
                    and 0 <= account["available_minor"] + delta <= MAX_SAFE_INTEGER):
                raise ServiceError("LIMIT_EXCEEDED", "余额超出整数金额范围。")
            entry_id = "entry_" + uuid4().hex
            connection.execute("UPDATE accounts SET balance_minor=balance_minor+?,available_minor=available_minor+? WHERE account_id=?",
                               (delta, delta, command["account_id"]))
            connection.execute("""INSERT INTO ledger_entries (transaction_id,account_id,reference_id,posting_key,
                amount_minor,direction,kind,category,occurred_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (entry_id, command["account_id"], command["reference_id"], command["posting_key"], amount,
                 command["effect"], command["kind"], "转账", state["occurred_at"]))
            receipt = dict(posting_key=command["posting_key"], entry_id=entry_id,
                           account_id=command["account_id"], reference_id=command["reference_id"],
                           effect=command["effect"], amount_minor=amount)
            validate_funds_receipt(command, receipt)
            connection.execute("INSERT INTO fund_postings VALUES (?,?,?,?,?)",
                (command["posting_key"], principal_id, entry_id, json.dumps(command), json.dumps(receipt)))
            return receipt  # 仅为外层事务内回执，此处没有 commit。

    def get_context(self, *, principal_id):
        from .merchant import ITEMS, ADDRESSES
        with self.store.connection() as connection:
            require_principal(connection, principal_id)
            accounts = [dict(row) for row in connection.execute(
                "SELECT account_id,display_name FROM accounts WHERE principal_id=? ORDER BY account_id",
                (principal_id,))]
            holdings=[dict(row) for row in connection.execute(
                """SELECT holding_id,account_id,product_id,units FROM holdings
                   WHERE principal_id=? AND state='open' ORDER BY holding_id""", (principal_id,))]
            return validate_context(dict(schema_version=SCHEMA_VERSION, source="sandbox", accounts=accounts,
                products=products(), holdings=holdings, cards=[dict(row) for row in connection.execute(
                    '''SELECT card_id,account_id,card_state,limit_minor FROM cards JOIN accounts USING(account_id)
                       WHERE principal_id=? ORDER BY card_id''', (principal_id,))],
                items=[dict(i) for i in ITEMS], addresses=[dict(a) for a in ADDRESSES], questionnaires=[]))

    def query(self, action, params, *, principal_id):
        # ponytail: 单机小规模查询用短事务保存游标；吞吐量扩大时再拆分读写事务。
        with self.store.connection(write=action == "transaction.list") as connection:
            require_principal(connection, principal_id)
            if action == 'scenario.plan':
                from scenario import plan
                try:
                    return plan(params['goal'],self.clock().astimezone(timezone(timedelta(hours=8))).date())
                except ValueError as exc:
                    raise ServiceError('INVALID_REQUEST',str(exc)) from exc
            if action == 'wealth.compare':
                from wealth import compare
                return compare(params)
            if action == "account.balance":
                account = owned_account(connection, params["account_id"], principal_id)
                return {name: account[name] for name in ("account_id", "balance_minor", "available_minor",
                                                        "reserved_minor", "currency")}
            if action == "payee.resolve":
                field = "display_name" if params["match_type"] == "name" else "phone"
                rows = connection.execute(f"""SELECT payee_id,display_name,masked_account,bank_name
                    FROM payees WHERE principal_id=? AND {field}=? ORDER BY payee_id""",
                    (principal_id, params["query"].strip())).fetchall()
                return {"candidates": [{k: row[k] for k in row.keys() if row[k] is not None} for row in rows]}
            if action == "transaction.list":
                return self._transactions(connection, params, principal_id)
            if action == "bill.analyze":
                owned_account(connection, params["account_id"], principal_id)
                # 同一只读事务内遍历完整账户流水，不经会写游标的分页入口。
                # 日期筛选与金额汇总统一交给账单核心；不截取前 100 条。
                with closing(connection.execute("""SELECT transaction_id,account_id,amount_minor,
                    direction,kind,category,occurred_at,merchant_name FROM ledger_entries
                    WHERE account_id=? ORDER BY occurred_at,transaction_id""",
                    (params["account_id"],))) as rows:
                    return analyze(params, ({k: row[k] for k in row.keys() if row[k] is not None}
                                            for row in rows))
            if action == "operation.get":
                row = connection.execute("""SELECT * FROM operations
                    WHERE principal_id=? AND action=? AND idempotency_key=?""",
                    (principal_id, params["original_action"], params["original_idempotency_key"])).fetchone()
                if row is None:
                    # 不泄露其他用户是否持有同一键；NOT_FOUND 不能证明业务从未执行。
                    raise ServiceError("NOT_FOUND", "未找到当前用户的原操作，请继续核实。")
                request = loads_request(row["request_json"])
                result = loads_result(request, row["result_json"])
                if (request["action"] != row["action"] or request.get("idempotency_key") != row["idempotency_key"]
                        or operation_digest(request, principal_id=principal_id) != row["digest"]):
                    raise ServiceError("UPSTREAM_ERROR", "原操作记录不一致，请核实。")
                return {"operation_request": request, "operation_result": result}
            raise ServiceError("INVALID_REQUEST", "公开入口仅开放资源和业务查询，写操作尚未开放。")

    @staticmethod
    def _transactions(connection, params, principal_id):
        account = params["account_id"]
        owned_account(connection, account, principal_id)
        binding = (principal_id, account, params["start_date"], params["end_date"])
        after_time, after_id = "", ""
        if "cursor" in params:
            saved = connection.execute("SELECT * FROM page_cursors WHERE token=?", (params["cursor"],)).fetchone()
            if saved is None or tuple(saved[k] for k in ("principal_id", "account_id", "start_date", "end_date")) != binding:
                raise ServiceError("INVALID_REQUEST", "分页游标无效或不属于当前查询。")
            upper_seq, after_time, after_id = saved["upper_seq"], saved["after_time"], saved["after_id"]
        else:
            upper_seq = connection.execute("SELECT COALESCE(MAX(seq),0) FROM ledger_entries WHERE account_id=?",
                                           (account,)).fetchone()[0]
        limit = params.get("limit", 100)
        shanghai = timezone(timedelta(hours=8))
        # 不使用 SQLite date()：其毫秒舍入会把 23:59:59.999999 误判为次日。
        try:
            lower = utc_timestamp(datetime.combine(date.fromisoformat(params["start_date"]),
                                                   time.min, shanghai).isoformat())
        except ContractError:
            if params["start_date"] != "0001-01-01":
                raise
            lower = "0001-01-01T00:00:00.000000Z"
        upper = utc_timestamp(datetime.combine(date.fromisoformat(params["end_date"]),
                                               time.max, shanghai).isoformat())
        rows = connection.execute("""SELECT transaction_id,account_id,amount_minor,direction,kind,category,
                occurred_at,merchant_name FROM ledger_entries
            WHERE account_id=? AND seq<=? AND occurred_at BETWEEN ? AND ?
                AND (occurred_at,transaction_id)>(?,?)
            ORDER BY occurred_at,transaction_id LIMIT ?""",
            (account, upper_seq, lower, upper, after_time, after_id, limit+1)).fetchall()
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit-1]
            next_cursor = "page_" + uuid4().hex
            connection.execute("INSERT INTO page_cursors VALUES (?,?,?,?,?,?,?,?)",
                               (next_cursor, *binding, upper_seq, last["occurred_at"], last["transaction_id"]))
        return {"transactions": [{k: row[k] for k in row.keys() if row[k] is not None} for row in rows[:limit]],
                "next_cursor": next_cursor}
