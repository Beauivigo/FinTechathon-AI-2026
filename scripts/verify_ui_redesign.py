"""Full UI-release regression with isolated data and dedicated evidence paths."""
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bank import seed


def main():
    output = ROOT / 'docs/evidence/ui-bank-20261023'
    output.mkdir(parents=True, exist_ok=True)
    runtime = ROOT / 'runtime/sandbox.sqlite3'
    fingerprint = lambda: sha256(runtime.read_bytes()).hexdigest() if runtime.is_file() else None
    before = fingerprint()
    with (output / 'full.txt').open('w', encoding='utf-8') as log:
        with redirect_stdout(log), redirect_stderr(log), TemporaryDirectory() as temporary, patch('bank.seed.PROJECT_ROOT', Path(temporary)):
            suite = unittest.TestLoader().discover(str(ROOT / 'tests'))
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    unchanged = fingerprint() == before
    record = dict(executor='Codex independent acceptance', command='python -B -X utf8 scripts/verify_ui_redesign.py',
                  run=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                  runtime_unchanged=unchanged, passed=result.wasSuccessful() and not result.skipped and unchanged,
                  scope='Full current suite, temporary simulated databases; no live model requests.',
                  source_sha256={str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
                                 for folder in ('app','agent','bank','shared','tests')
                                 for path in (ROOT/folder).rglob('*.py')})
    (output / 'full.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:record[key] for key in ('run','failures','errors','skipped','runtime_unchanged','passed')}))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
