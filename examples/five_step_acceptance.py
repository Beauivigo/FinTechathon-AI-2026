"""逐轮自动验收：任一轮失败或跳过即停止，最后执行全库和原有演示。"""
import contextlib
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
ROUNDS = [
    ('C 只读账单', ['material_access', 'material_reader', 'material_checks', 'material_reconcile',
                   'material_report', 'material_v12', 'billing', 'billing_page']),
    ('产品对比', ['wealth', 'bank', 'app', 'billing_page']),
    ('公共授权', ['gateway_extension', 'gateway', 'bank', 'app']),
    ('卡片四动作', ['cards', 'gateway_extension', 'gateway', 'bank', 'app']),
    ('AA 收款单', ['split', 'cards', 'gateway_extension', 'gateway', 'bank', 'app']),
]


def main():
    report = dict(python=platform.python_version(), rounds=[], examples=[], passed=False)
    output = ROOT / 'local_reports'
    output.mkdir(exist_ok=True)
    loader = unittest.TestLoader()
    for label, modules in ROUNDS + [('全库回归', None)]:
        print('验收：' + label, flush=True)
        suite = (loader.loadTestsFromNames(['tests.test_' + name for name in modules]) if modules
                 else loader.discover(str(ROOT / 'tests')))
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        passed = result.wasSuccessful() and not result.skipped
        row = dict(name=label, tests=result.testsRun, failures=len(result.failures),
                   errors=len(result.errors), skipped=len(result.skipped), passed=passed)
        report['rounds'].append(row)
        (output / f'five-step-{len(report["rounds"])}.log').write_text(log.getvalue(), encoding='utf-8')
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if not passed:
            break
    if len(report['rounds']) == 6 and all(row['passed'] for row in report['rounds']):
        for name in ('contract_demo', 'gateway_demo', 'funds_demo', 'billing_demo'):
            completed = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-m', 'examples.' + name],
                cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60)
            report['examples'].append(dict(name=name, exit_code=completed.returncode))
            (output / ('five-step-' + name + '.log')).write_text(completed.stdout + completed.stderr, encoding='utf-8')
            if completed.returncode:
                break
        report['passed'] = len(report['examples']) == 4 and all(x['exit_code'] == 0 for x in report['examples'])
    (output / 'five-step-acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
