from pathlib import Path
import sys,json,tempfile
from copy import deepcopy
from datetime import datetime,timezone
from unittest.mock import patch
ROOT=Path.cwd();sys.path.insert(0,str(ROOT))
from bank.store import Store
from bank.seed import initialize
from bank.gateway import BankGateway
from streamlit.testing.v1 import AppTest
out=ROOT/'docs/evidence/project-audit-20261023'
with tempfile.TemporaryDirectory() as td,patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No real HTTP')):
    store=Store(Path(td)/'bank.sqlite3');initialize(store)
    gateway=BankGateway(store,clock=lambda:datetime(2026,10,23,3,tzinfo=timezone.utc))
    plan={'action':'plan','slots':{'account_id':'demo_account_a'},'tasks':[{'id':'pay','title':'给李明20元','intent':'transfer','slots':{'payee_query':'李明','amount_text':'20元'},'depends_on':[]},{'id':'card','title':'冻结卡片','intent':'card','slots':{'card_id':'CARD-E001','card_operation':'冻结'},'depends_on':['pay']}]}
    with patch('app.demo.gateway',return_value=gateway),patch('agent.model_client.ModelClient.__call__') as model:
        model.return_value=json.dumps(plan,ensure_ascii=False)
        app=AppTest.from_file(str(ROOT/'app/pages/0_AI银行助手.py'),default_timeout=15).run()
        app.text_area(key='bank_model_input').set_value('给李明20元，然后冻结卡片')
        app.button(key='bank_model_send').click().run()
        app.button(key='model_prepare_step').click().run()
        app.button(key='action_approve').click().run();assert not app.exception
        runner=app.session_state['model_runner']
        first_key=runner.tasks[0]['operation_key'];assert runner.tasks[0]['status']=='completed'
        assert runner.tasks[1]['status']=='pending'
        revised=deepcopy(plan);revised['tasks'][1]['slots']['card_operation']='挂失';revised['tasks'][1]['title']='挂失卡片'
        model.return_value=json.dumps(revised,ensure_ascii=False)
        app.text_area(key='bank_model_input').set_value('第二步改为挂失，第一步已经办过了')
        app.button(key='bank_model_send').click().run();assert not app.exception
        runner=app.session_state['model_runner'];assert runner.receipts=={} and runner.tasks[0]['status']=='pending'
        app.button(key='model_prepare_step').click().run()
        second_key=app.session_state['model_runner'].tasks[0]['operation_key']
        app.button(key='action_approve').click().run();assert not app.exception
        with store.connection() as c:
            count=c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0]
            balance=c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0]
        assert first_key!=second_key and count==2 and balance==196000
        record=dict(reproduced=True,model='deterministic offline response repeating same task IDs in an updated full plan',first_operation_key=first_key,second_operation_key=second_key,transfers=count,final_balance_minor=balance,impact='Editing only the unexecuted second step replaces runner and loses completed first-step receipts; same first task can be paid again with a new key, after a new confirmation. No real model reliability claim.')
(out/'plan-revision.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(record,ensure_ascii=False))
