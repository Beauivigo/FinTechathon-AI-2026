from pathlib import Path
import sys,json,hashlib,subprocess,tempfile,unittest,time,faulthandler
from unittest.mock import patch
from contextlib import redirect_stdout,redirect_stderr
root=Path.cwd();sys.path.insert(0,str(root))
out=root/'docs/evidence/project-audit-20261023'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
files=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],encoding='utf-8').split('\0')
source={f:sha(root/f) for f in files if f.endswith(('.py','.toml')) and (root/f).is_file()}
runtime={str(p.relative_to(root)):sha(p) for p in (root/'runtime').rglob('*') if p.is_file()}
profile=root/'.cache/bank-model/profile.json';profile_before=sha(profile)
started=time.time()
with (out/'full.txt').open('w',encoding='utf-8') as log,(out/'diagnostic.txt').open('w',encoding='utf-8') as diag:
    faulthandler.dump_traceback_later(180,file=diag,exit=True)
    with redirect_stdout(log),redirect_stderr(log),tempfile.TemporaryDirectory() as td,patch('bank.seed.PROJECT_ROOT',Path(td)),patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('Audit blocks real HTTP')):
        suite=unittest.TestLoader().discover(str(root/'tests'))
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    faulthandler.cancel_dump_traceback_later()
record=dict(executor='review independent local audit',base_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),python=sys.version,source_sha256=source,run=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),seconds=round(time.time()-started,2),runtime_unchanged=runtime=={str(p.relative_to(root)):sha(p) for p in (root/'runtime').rglob('*') if p.is_file()},profile_unchanged=sha(profile)==profile_before,source_unchanged=all(sha(root/f)==h for f,h in source.items()),passed=result.wasSuccessful() and not result.skipped,real_http_blocked=True,scope='Offline tests with isolated temporary bank data; no paid model calls. Not proof of real MFA or bank integration.')
(out/'full.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in record.items() if k!='source_sha256'},ensure_ascii=False))

