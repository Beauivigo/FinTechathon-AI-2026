"""U3/U4 stage acceptance helper (not a broker frozen-test record) with hash-pinned test modules."""
import hashlib
import sys
import unittest
from pathlib import Path

HASHES = {
    'tests/test_u3_background.py': 'cfaa8865cd7107e2b17f550a8e9052afb04b5bb1a047895a58b898058854cae2',
    'tests/test_u4_launcher.py': '543e699469fb0ddd0bb71203beadfd724cb14b40fc05965f32a3656ae877d7f0',
}


def _root():
    here = Path(globals().get('__file__', Path.cwd())).resolve()
    for candidate in (Path.cwd(), here.parent, here.parent.parent):
        if (candidate / 'bank' / 'auth.py').is_file() and (candidate / 'tests' / 'test_u3_background.py').is_file():
            return candidate
    raise RuntimeError('cannot locate the project root for the frozen U3/U4 checks')


def _verify_hashes():
    root = _root()
    for name, expected in HASHES.items():
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if actual != expected:
            raise AssertionError('frozen U3/U4 test changed: ' + name)


def __review_acceptance__():
    root = _root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    _verify_hashes()
    from tests.test_u3_background import BackgroundTests
    from tests.test_u4_launcher import LauncherTests, LifecycleTests, SyntaxTests
    suite = unittest.TestSuite()
    for case in (BackgroundTests, LauncherTests, LifecycleTests, SyntaxTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    return suite


if __name__ == '__main__':
    unittest.main(verbosity=2)
