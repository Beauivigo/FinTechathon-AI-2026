import sys,runpy
from pathlib import Path
from unittest.mock import patch
r=Path(r"C:\Users\huang\.codex\delegates\deepseek\runs\070d8ef9-1a28-4616-8bfa-f5578913d13f")
sys.path.insert(0,str(r/'worktree'));sys.argv=[str(r/'desktop-frozen'/sys.argv[1])]
with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('Offline acceptance forbids HTTP')):
 runpy.run_path(sys.argv[0],run_name='__main__')
