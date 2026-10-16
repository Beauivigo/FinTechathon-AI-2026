"""B答复采纳复测，A实际执行；不宣称还原B历史15项或预算实现。"""
from pathlib import Path
from hashlib import sha256
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json, subprocess, sys, unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
OUT=Path(__file__).with_suffix('')
def digest(path):return sha256(path.read_bytes()).hexdigest() if path.exists() else None
runtime=ROOT/'runtime/sandbox.sqlite3';before=digest(runtime)
modules=['tests.test_scheduling','tests.test_recovery','tests.test_split']
with TemporaryDirectory() as td,patch('bank.seed.PROJECT_ROOT',Path(td)):
    suite=unittest.TestLoader().loadTestsFromNames(modules)
    with OUT.with_suffix('.txt').open('w',encoding='utf-8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
record=dict(executor='A本次补测，非B历史15项',commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
    command='.venv/Scripts/python.exe -B -X utf8 docs/evidence/2026-10-17-b-response-retest.py',modules=modules,
    run=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
    runtime_unchanged=before==digest(runtime),
    materials={p.name:digest(p) for p in (ROOT/'materials/reviewed/2026-10-17-B').iterdir() if p.suffix in ('.docx','.xlsx')},
    limits=['预算/merchant.order未实现，本次不认证预算unknown释放可用。',
            'B历史15项仅收到文档声明，未取得其evidence3/4及对应脚本。',
            'B-05同账户场景覆盖200000→120000待verify→60000；跨账户累计算例不可混写。'])
record['passed']=result.wasSuccessful() and not result.skipped and record['runtime_unchanged']
OUT.with_suffix('.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(record,ensure_ascii=False,indent=2))
raise SystemExit(0 if record['passed'] else 1)
