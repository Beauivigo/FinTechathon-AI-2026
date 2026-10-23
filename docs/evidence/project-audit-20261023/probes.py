"""Independent audit probes: deterministic model responses and temporary ledgers only.
Assertions reproduce current defects; they are NOT release acceptance tests.
"""
from pathlib import Path
import sys,json,tempfile
from datetime import datetime,timezone
from unittest.mock import patch
ROOT=Path.cwd();sys.path.insert(0,str(ROOT))
from bank.store import Store
from bank.seed import initialize
from bank.gateway import BankGateway
from agent.controller import Conversation
from agent.model_dialogue import DialogueSession
from agent.model_bank import BankPlanRunner
from streamlit.testing.v1 import AppTest
OUT=ROOT/'docs/evidence/project-audit-20261023'
records={}
with tempfile.TemporaryDirectory() as td,patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No real HTTP in audit')):
    store=Store(Path(td)/'bank.sqlite3');initialize(store)
    gateway=BankGateway(store,clock=lambda:datetime(2026,10,23,3,tzinfo=timezone.utc))
    model_flow=Conversation(gateway,principal_id='demo_user_a')
    model_flow.start_action('transfer.create',dict(account_id='demo_account_a',payee_id='demo_payee_li',amount_minor=2000))
    original=gateway.authorize
    def lose_reply(*args,**kwargs):
        original(*args,**kwargs)
        raise OSError('Injected lost reply after commit')
    with patch.object(gateway,'authorize',side_effect=lose_reply):model_flow.decide(True)
    assert model_flow.needs_reconciliation and model_flow.result['status']=='unknown'
    manual=Conversation(gateway,principal_id='demo_user_a')
    app=AppTest.from_file(str(ROOT/'app/main.py'),default_timeout=15)
    app.session_state['conversation']=manual
    app.session_state['model_bank_flow']=model_flow
    app.query_params['view']='transfer'
    app.run();assert not app.exception
    enabled=not app.chat_input[0].disabled
    assert enabled and not manual.blocked
    app.chat_input[0].set_value('给李明转20元').run()
    assert not app.exception
    next(b for b in app.button if b.label=='确认此笔模拟转账').click().run()
    assert not app.exception
    with store.connection() as c:
        balance=c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0]
        count=c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0]
    assert balance==196000 and count==2
    records['cross_page_unknown']=dict(reproduced=True,original_ai_status=model_flow.result['status'],original_ai_needs_reconciliation=model_flow.needs_reconciliation,manual_chat_enabled=enabled,transfers=count,initial_balance_minor=200000,final_balance_minor=balance,impact='After AI transfer commits but loses reply, manual page permits new-key repeat transfer before querying original. Second transfer still requires a new confirmation; no authorization bypass.')
    audit=lambda e:gateway.record_agent_event(e,principal_id='demo_user_a')
    value={'action':'plan','tasks':[{'id':'reserve','title':'预算','intent':'budget_reserve','slots':{'account_id':'demo_account_a','amount_text':'357元','purpose':'生日'},'depends_on':[]},{'id':'balance_after','title':'查余额','intent':'balance','slots':{'account_id':'demo_account_a'},'depends_on':['reserve']}]}
    session=DialogueSession(lambda messages:value,audit=audit)
    snap=session.submit('预留357元后查询余额')
    assert snap['phase']=='plan'
    with store.connection() as c:
        events=[dict(r) for r in c.execute('SELECT action,outcome,details_json FROM audit_events WHERE trace_id=?',(session.trace_id,))]
        tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    records['plan_persistence']=dict(phase=snap['phase'],task_count=len(snap['tasks']),events=events,table_names=tables,note='Accepted task IDs, normalized fields and dependency edges are kept only in DialogueSession/BankPlanRunner session memory; database events contain phase/version/failure/intent metadata.')
(OUT/'probes.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(records,ensure_ascii=False,indent=2))
