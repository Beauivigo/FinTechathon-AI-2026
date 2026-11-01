"""多服务商变更的独立全量验收；不调用真实模型，不改日常数据。"""
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


def fingerprint(path):
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    output = ROOT / 'docs/evidence/model-providers-20261023'
    output.mkdir(parents=True, exist_ok=True)
    runtime = ROOT / 'runtime/sandbox.sqlite3'
    profile = ROOT / '.cache/bank-model/profile.json'
    before = fingerprint(runtime), fingerprint(profile)
    with (output / 'full.txt').open('w', encoding='utf-8') as log:
        with redirect_stdout(log), redirect_stderr(log), TemporaryDirectory() as temporary, \
                patch('bank.seed.PROJECT_ROOT', Path(temporary)), \
                patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Offline acceptance blocks real HTTP requests')):
            suite = unittest.TestLoader().discover(str(ROOT / 'tests'))
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    runtime_unchanged = fingerprint(runtime) == before[0]
    profile_unchanged = fingerprint(profile) == before[1]
    record = dict(executor='review independent acceptance', date='2026-10-23',
        command='python -B -X utf8 scripts/verify_model_providers.py',
        run=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        runtime_unchanged=runtime_unchanged, saved_profile_unchanged=profile_unchanged,
        passed=result.wasSuccessful() and not result.skipped and runtime_unchanged and profile_unchanged,
        real_http_blocked=True, scope='Full current suite, isolated simulated databases and temporary credentials; real urllib HTTP is blocked.',
        source_sha256={str(path.relative_to(ROOT)):fingerprint(path)
            for folder in ('app', 'agent', 'bank', 'shared', 'tests') for path in (ROOT/folder).rglob('*.py')})
    (output / 'full.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key:record[key] for key in ('run','failures','errors','skipped','runtime_unchanged','saved_profile_unchanged','passed')}))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
