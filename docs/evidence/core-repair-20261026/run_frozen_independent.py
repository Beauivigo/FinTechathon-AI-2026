from pathlib import Path
import sys,tempfile,runpy
from unittest.mock import patch
root=Path(__file__).resolve().parents[3]
r=Path(r"C:\Users\huang\.review\delegates\deepseek\runs\80acd3d5-c455-4bb8-8b54-c0f1f86f123d")
sys.path.insert(0,str(r/'worktree'))
tempfile.tempdir=str(root/'.cache/core-repair-20261026/tmp')
stage=sys.argv[1];sys.argv=[str(r/'desktop-frozen'/stage)]
with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('Offline acceptance forbids HTTP')):
    runpy.run_path(sys.argv[0],run_name='__main__')
