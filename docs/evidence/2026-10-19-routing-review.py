"""Codex固定验收执行器：在普通沙盒加载隔离产物，不复制至主工作区。"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_id')
    args = parser.parse_args()
    run = Path('C:/Users/huang/.codex/delegates/deepseek/runs')/args.run_id
    source = run/'worktree/agent/routing.py'
    result = json.loads((run/'result.json').read_text(encoding='utf-8'))
    returned = {f['path']: f['content'] for f in result.get('files', [])}
    assert set(returned) == {'agent/routing.py'}, 'Unexpected returned scope'
    assert source.read_text(encoding='utf-8') == returned['agent/routing.py']
    load('agent.routing', source)
    tests = load('codex_routing_acceptance', ROOT/'tests/test_routing_acceptance.py')
    prefix = ROOT/'docs/evidence'/('2026-10-19-'+args.run_id)
    with prefix.with_suffix('.txt').open('w', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(tests))
    passed = result.wasSuccessful() and not result.skipped
    record = dict(reviewer='Codex', status='accepted' if passed else 'rejected', run_id=args.run_id,
                  commands=[dict(command=' '.join(sys.argv), tests=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped))],
                  inspectedFileHashes={str(source):hashlib.sha256(source.read_bytes()).hexdigest()},
                  acceptanceHashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    (ROOT/'tests/test_routing_acceptance.py', ROOT/'docs/evidence/2026-10-19-routing-cases.json')},
                  inspected=['review-summary.json','diff.patch','result.json','actual source'], integrated=False)
    body = json.dumps(record, ensure_ascii=False, indent=2)+'\n'
    prefix.with_suffix('.json').write_text(body, encoding='utf-8')
    try:
        (run/'codex-review.json').write_text(body, encoding='utf-8')
        review_saved = True
    except PermissionError:
        review_saved = False
    print(json.dumps(dict(passed=passed,run=result.testsRun,failures=len(result.failures),errors=len(result.errors),external_review_saved=review_saved)))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
