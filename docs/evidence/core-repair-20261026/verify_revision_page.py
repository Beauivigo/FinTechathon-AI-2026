from pathlib import Path
import sys,json,tempfile
from datetime import datetime,timezone
from unittest.mock import patch
root=Path(__file__).resolve().parents[3]
target=root/'.cache/core-repair-20261026/acceptance-tree'
sys.path.insert(0,str(target))
from streamlit.testing.v1 import AppTest
from bank import seed
from bank.store import Store
from bank.gateway import BankGateway
now=datetime(2026,10,26,3,tzinfo=timezone.utc)
with tempfile.TemporaryDirectory() as td, patch('bank.seed.PROJECT_ROOT',Path(td)), patch('app.demo.demo_path',new=seed.demo_path), patch('app.demo.BankGateway',side_effect=lambda *a,**kw:BankGateway(*a,**kw,clock=lambda:now)), patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No HTTP')):
    app=AppTest.from_file(str(target/'app/pages/0_AI银行助手.py'),default_timeout=20).run()
    assert not app.exception
    store=Store(Path(td)/'runtime/sandbox.sqlite3')
    resources=BankGateway(store,clock=lambda:now).get_context(principal_id='demo_user_a')
    fields=dict(account_id='demo_account_a',birthday='2026-11-20',budget_text='1000元',item_ids=[resources['items'][i]['item_id'] for i in (0,2)],delivery_address_id=resources['addresses'][0]['delivery_address_id'],budget_at='2026-11-05T14:00:00+08:00',purchase_at='2026-11-18T15:00:00+08:00')
    def submit(fields,text):
        output=dict(reply='请核对草稿',action='propose',intent='birthday',slots=fields)
        with patch('agent.model_client.ModelClient.__call__',return_value=json.dumps(output,ensure_ascii=False)):
            app.text_area(key='bank_model_input').set_value(text)
            app.button(key='bank_model_send').click().run()
        assert not app.exception
    submit(fields,'按指定时间安排生日预算和采购')
    old=app.session_state['model_runner'];times=[t['not_before'] for t in old.tasks]
    revised={k:v for k,v in fields.items() if k not in ('budget_at','purchase_at')};revised['budget_text']='1100元'
    submit(revised,'仅把预算改为1100元')
    new=app.session_state['model_runner']
    assert new is not old
    assert [t['not_before'] for t in new.tasks]==times
    assert new.tasks[0]['fields']['amount_text']=='1100.00'
    submit(dict(revised,birthday='2026-11-22'),'将生日改到22日，时间没有给出')
    assert app.session_state['model_runner'] is new
    assert [t['not_before'] for t in app.session_state['model_runner'].tasks]==times
    with store.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM operations').fetchone()[0]==0
    print(json.dumps(dict(passed=True,checks=['complete birthday UI','budget revision preserves custom times','invalid date revision preserves saved plan','no business operations'],real_http_blocked=True)))

