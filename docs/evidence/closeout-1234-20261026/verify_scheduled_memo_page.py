from pathlib import Path
from datetime import datetime,timezone
import sys,tempfile,json
from unittest.mock import patch
root=Path(__file__).resolve().parents[3];target=root/'.cache/closeout-1234-20261026/acceptance-tree'
sys.path.insert(0,str(target))
from bank import seed
from bank.gateway import BankGateway
from bank.store import Store
from streamlit.testing.v1 import AppTest
now=datetime(2026,10,26,3,tzinfo=timezone.utc)
output=dict(reply='草稿',action='propose',intent='scheduled_transfer',slots=dict(account_id='demo_account_a',payee_query='李明',amount_text='20元',execute_at='2026-10-26T11:05:00+08:00',memo='生日快乐'))
with tempfile.TemporaryDirectory() as td,patch('bank.seed.PROJECT_ROOT',Path(td)),patch('app.demo.demo_path',new=seed.demo_path),patch('app.demo.BankGateway',side_effect=lambda *a,**kw:BankGateway(*a,**kw,clock=lambda:now)),patch('agent.model_client.ModelClient.__call__',return_value=json.dumps(output,ensure_ascii=False)),patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No HTTP')):
 app=AppTest.from_file(str(target/'app/pages/0_AI银行助手.py'),default_timeout=20).run()
 app.text_area(key='bank_model_input').set_value('给李明定时转20元，备注生日快乐')
 app.button(key='bank_model_send').click().run()
 app.button(key='model_prepare_step').click().run()
 assert not app.exception
 assert any('转账备注：生日快乐' in x.value for x in app.text)
 app.button(key='action_approve').click().run()
 assert not app.exception
 assert app.session_state['model_runner'].flow.result['status']=='scheduled'
 fresh=AppTest.from_file(str(target/'app/pages/0_AI银行助手.py'),default_timeout=20).run()
 fresh.button(key='restore_plan').click().run()
 assert not fresh.exception
 assert fresh.session_state['model_runner'].flow.request['params']['memo']=='生日快乐'
 assert any('转账备注：生日快乐' in x.value for x in fresh.text)
 with Store(Path(td)/'runtime/sandbox.sqlite3').connection() as c:assert c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0]==0
 print(json.dumps(dict(passed=True,checks=['trusted memo displayed','schedule registration','fresh page restoration','no premature transfer'],real_http_blocked=True)))
