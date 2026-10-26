"""Offline U1-U5 service/lifecycle test runner with real pyotp.

It runs the new service/lifecycle tests plus a focused business-regression
subset that does not require Streamlit.  Temporary databases live inside a
workspace temp directory, never in ``runtime/``.  The JSON record deliberately
states that real Streamlit pages and real pyotp/qrcode integration are only
checked by the full-dependency acceptance run.
"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

U_TESTS = ('tests.test_u1_setup', 'tests.test_u2_flows',
           'tests.test_u3_background', 'tests.test_u4_launcher')
REGRESSION = ('tests.test_gateway', 'tests.test_gateway_extension', 'tests.test_funds',
              'tests.test_merchant', 'tests.test_plan_persistence',
              'tests.test_worker_birthday', 'tests.test_six_scenarios',
              'tests.test_recovery', 'tests.test_subscription_charge',
              'tests.test_real_auth')
SOURCES = (
    'app/auth_ui.py', 'app/background_panel.py', 'app/lifecycle_panel.py',
    'app/main.py', 'app/workflow.py', 'bank/auth.py', 'bank/background.py',
    'bank/first_run.py', 'bank/lifecycle.py', 'bank/store.py', 'bank/worker.py',
    'launcher.py', '启动银行助手.py', 'tests/test_u1_setup.py', 'tests/test_u2_flows.py',
    'tests/test_u3_background.py', 'tests/test_u4_launcher.py',
    'tests/test_u_stage2_acceptance.py', 'tests/test_u_stage3_acceptance.py',
    'scripts/verify_u_core.py',
    'docs/U1-U5程序内使用说明.md', 'README.md',
)


def fingerprint():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def main():
    parser = argparse.ArgumentParser(description='Run offline U1-U5 self-tests and write a JSON record')
    parser.add_argument('--output', default='docs/evidence/u1-u5-latest')
    args = parser.parse_args()
    out = ROOT / args.output
    if out.exists():
        print('输出目录已存在，请换一个新目录，避免覆盖旧证据：' + str(out))
        return 2
    out.mkdir(parents=True)
    temp_root = Path(tempfile.mkdtemp(prefix='.u-selftest-', dir=ROOT))
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(temp_root)
    before = fingerprint()
    # Final checks use the installed pyotp dependency; no protocol substitute.
    import tests.test_u1_setup  # noqa: F401

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in (*U_TESTS, *REGRESSION):
        suite.addTests(loader.loadTestsFromName(name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    tempfile.tempdir = previous_tempdir
    assert temp_root.resolve().is_relative_to(ROOT.resolve())
    shutil.rmtree(temp_root)
    after = fingerprint()
    try:
        import pyotp  # noqa: F401
        pyotp_mode = 'real' if getattr(pyotp, '__file__', None) else 'test-stand-in'
    except Exception:
        pyotp_mode = 'missing'
    try:
        import streamlit  # noqa: F401
        streamlit_available = True
    except Exception:
        streamlit_available = False
    record = dict(
        executed_at=datetime.now(timezone.utc).isoformat(),
        command='python -B -X utf8 scripts/verify_u_core.py --output ' + args.output,
        python=sys.version,
        tests_run=result.testsRun,
        failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        passed=result.wasSuccessful(),
        pyotp_mode=pyotp_mode,
        streamlit_available=streamlit_available,
        source_sha256=before,
        source_unchanged=before == after,
        scope='Temporary simulated banks only. Service/authorization/lifecycle/concurrency/'
              'launcher checks. No real runtime data, credentials, model calls, network, '
              'Streamlit pages, real phone binding or deployment.',
        not_verified='Streamlit UI with full dependencies, real pyotp/qrcode integration, '
                     'user-owned phone enrollment, real model providers, packaging/cloud.',
        failure_details=[[str(test), text] for test, text in result.failures + result.errors],
    )
    (out / 'selftest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: record[key] for key in ('passed', 'tests_run', 'failures', 'errors',
                                                   'skipped', 'source_unchanged', 'pyotp_mode',
                                                   'streamlit_available')}, ensure_ascii=False))
    return 0 if record['passed'] and record['source_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
