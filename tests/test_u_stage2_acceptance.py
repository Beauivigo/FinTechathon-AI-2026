"""U1/U2 stage acceptance helper (not a broker frozen-test record).

The test modules themselves are hash-pinned so a later edit cannot silently
weaken the frozen checks.  The runner is intentionally stdlib-only; the U1
module requires real pyotp; missing dependencies fail explicitly.
"""
import hashlib
import sys
import unittest
from pathlib import Path

HASHES = {
    'tests/test_u1_setup.py': '272f88dc84ea9ccc8d7c052e21c26b740facae4ff2d513fcd630f2860821ce92',
    'tests/test_u2_flows.py': '8fdd827400ebb136997ce4d6528dc159707da3a00308f32878e91cb390f0ec3f',
}


def _root():
    here = Path(globals().get('__file__', Path.cwd())).resolve()
    for candidate in (Path.cwd(), here.parent, here.parent.parent):
        if (candidate / 'bank' / 'auth.py').is_file() and (candidate / 'tests' / 'test_u1_setup.py').is_file():
            return candidate
    raise RuntimeError('cannot locate the project root for the frozen U1/U2 checks')


def _verify_hashes():
    root = _root()
    for name, expected in HASHES.items():
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if actual != expected:
            raise AssertionError('frozen U1/U2 test changed: ' + name)


def __codex_acceptance__():
    root = _root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    _verify_hashes()
    from tests.test_u1_setup import U1SetupTests
    from tests.test_u2_flows import U2FlowTests
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(U1SetupTests))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(U2FlowTests))
    return suite


if __name__ == '__main__':
    unittest.main(verbosity=2)
