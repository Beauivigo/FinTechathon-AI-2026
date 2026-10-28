from pathlib import Path
import os,sys
root=Path(__file__).resolve().parents[3]
target=root/'.cache/closeout-1234-20261026/acceptance-tree'
sys.path.insert(0,str(target))
os.environ['BANK_MODEL_PROVIDER']='deepseek'
os.environ['BANK_MODEL_USE_DESKTOP_PET']='1'
from scripts import evaluate_closeout_model as evaluator
evaluator.LEDGER=root/'.cache/2026-10-26-closeout-model-budget.json'
evaluator.EVIDENCE_DIR=root/'docs/evidence/closeout-1234-20261026'
raise SystemExit(evaluator.main(['--live','--suffix','independent-live']))
