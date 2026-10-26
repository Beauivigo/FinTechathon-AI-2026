from pathlib import Path
import sys,tempfile,json
from unittest.mock import patch
r=Path(r"C:\Users\huang\.codex\delegates\deepseek\runs\070d8ef9-1a28-4616-8bfa-f5578913d13f\worktree")
sys.path.insert(0,str(r))
from streamlit.testing.v1 import AppTest
with tempfile.TemporaryDirectory() as td,patch('tempfile.gettempdir',return_value=td),patch('app.auth_ui.gate',return_value=None):
 app=AppTest.from_file(str(r/'app/phone_acceptance.py'),default_timeout=20).run()
 first=len(app.title)+len(app.header)+len(app.button)
 app.run();second=len(app.title)+len(app.header)+len(app.button)
 print(json.dumps(dict(first_elements=first,rerun_elements=second,exceptions=len(app.exception))))
 assert second>0, 'Phone acceptance page disappears after a successful first render'
