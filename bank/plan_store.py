"""Trusted persistent execution journal; never stores chat, proofs or model credentials."""
from copy import deepcopy
import json
from datetime import datetime, timezone
from shared import ServiceError, validate_request, validate_result

PLAN_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS agent_plans (plan_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES principals,
       revision INTEGER NOT NULL, snapshot_json TEXT NOT NULL, updated_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS plan_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL REFERENCES agent_plans,
       revision INTEGER NOT NULL, event TEXT NOT NULL, snapshot_json TEXT NOT NULL, occurred_at TEXT NOT NULL)''',
)


class PlanStore:
    def __init__(self, store): self.store = store

    def list(self, principal):
        with self.store.connection() as c:
            return [dict(r) for r in c.execute('SELECT plan_id,revision,updated_at FROM agent_plans WHERE principal_id=? ORDER BY updated_at DESC',(principal,))]

    def load(self, plan_id, principal):
        with self.store.connection() as c:
            row=c.execute('SELECT * FROM agent_plans WHERE plan_id=? AND principal_id=?',(plan_id,principal)).fetchone()
            if row is None: raise ServiceError('NOT_FOUND','未找到当前用户的计划。')
            return json.loads(row['snapshot_json']), row['revision']

    def save(self, runner, event='progress'):
        # Closed projection: never persist flow objects, dialogue history or authorization payloads.
        snapshot=dict(tasks=deepcopy(runner.tasks),receipts=deepcopy(runner.receipts),waiting=runner.waiting,
                      paused=runner.paused,choices=deepcopy(runner.choices),automation=runner.automation)
        from agent.model_safety import check_json_tree, check_trace
        check_trace(runner.trace_id);check_json_tree(snapshot)
        for task in snapshot['tasks']:
            if 'request' in task: validate_request(task['request'])
            if task['id'] in snapshot['receipts'] and 'request' in task:
                validate_result(task['request'],snapshot['receipts'][task['id']])
        raw=json.dumps(snapshot,ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>262144: raise ServiceError('INVALID_REQUEST','计划记录过大，已停止。')
        now=datetime.now(timezone.utc).isoformat()
        with self.store.connection(write=True) as c:
            row=c.execute('SELECT principal_id,revision,snapshot_json FROM agent_plans WHERE plan_id=?',(runner.trace_id,)).fetchone()
            if row and (row['principal_id']!=runner.flow.principal_id or row['revision']!=runner.revision):
                raise ServiceError('INVALID_STATE','计划已由其他页面修改，请恢复最新版本。')
            if row and row['snapshot_json']==raw:return
            if not row and runner.revision!=0: raise ServiceError('INVALID_STATE','原计划记录缺失。')
            revision=runner.revision+1
            c.execute('INSERT INTO agent_plans VALUES (?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET revision=excluded.revision,snapshot_json=excluded.snapshot_json,updated_at=excluded.updated_at',
                      (runner.trace_id,runner.flow.principal_id,revision,raw,now))
            c.execute('INSERT INTO plan_events(plan_id,revision,event,snapshot_json,occurred_at) VALUES (?,?,?,?,?)',(runner.trace_id,revision,event,raw,now))
        runner.revision=revision
