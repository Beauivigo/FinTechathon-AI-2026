"""逐轮验收并保存本次证据；始终隔离日常演示库。"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fingerprint(path):
    return sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('round', choices=['R1','R2','R3','R4','full'])
    parser.add_argument('modules', nargs='*')
    args = parser.parse_args()
    output = ROOT/'docs/evidence'/('2026-10-17-'+args.round)
    runtime = ROOT/'runtime/sandbox.sqlite3'
    before = fingerprint(runtime)
    loader = unittest.TestLoader()
    with TemporaryDirectory() as td, patch('bank.seed.PROJECT_ROOT', Path(td)):
        suite = loader.loadTestsFromNames(args.modules) if args.modules else loader.discover(str(ROOT/'tests'))
        with output.with_suffix('.txt').open('w',encoding='utf-8') as log:
            result = unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    unchanged = fingerprint(runtime) == before
    passed = result.wasSuccessful() and not result.skipped and unchanged
    files = subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=ROOT,text=True,encoding='utf-8').split('\0')
    sources = {f:fingerprint(ROOT/f) for f in files if f.endswith('.py') and (ROOT/f).is_file()}
    material_hashes = {str(p.relative_to(ROOT)):fingerprint(p) for p in (ROOT/'materials/reviewed/2026-10-16').iterdir() if p.is_file()}
    record = dict(executor='A自动补测，非成员历史记录',round=args.round,
        command=' '.join(sys.argv),base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_sha256=sources, material_sha256=material_hashes, python=sys.version,run=result.testsRun,failures=len(result.failures),
        errors=len(result.errors),skipped=len(result.skipped),runtime_unchanged=unchanged,passed=passed,
        scope='独立临时库、明确标记的模拟数据；测试名称及断言见脚本/日志。不代表真实银行、真实认证或未实现业务通过。')
    output.with_suffix('.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:record[k] for k in ('round','run','failures','errors','skipped','runtime_unchanged','passed')}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
