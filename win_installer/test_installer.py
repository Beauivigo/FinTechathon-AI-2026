"""Frozen stage 1: installer payload, manifest, licences, NSIS contract and build guards."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from win_installer import build_installer as bi


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode('utf-8')
    path.write_bytes(data)


def fixture_source(root):
    write(root / 'app/__init__.py', '')
    write(root / 'app/main.py', 'import streamlit\n')
    write(root / 'app/pending_view.py', 'PENDING = 1\n')
    write(root / 'app/pages/0_AI银行助手.py', 'page = 1\n')
    write(root / 'bank/__init__.py', '')
    write(root / 'bank/seed.py', 'def demo_path():\n    return None\n')
    write(root / 'shared/__init__.py', '')
    write(root / 'shared/contracts.py', 'ACTIONS = {}\n')
    write(root / 'future_pkg/mod.py', 'VALUE = 1\n')
    write(root / 'requirements-app.txt', 'streamlit==1.65.0\n')
    write(root / '.streamlit/config.toml', '[server]\nheadless = true\n')
    write(root / 'tests/test_app.py', 'test = 1\n')
    write(root / 'docs/private.md', 'private\n')
    write(root / 'materials/secret.txt', 'private\n')
    write(root / 'runtime/sandbox.sqlite3', 'sqlite')
    write(root / '.cache/bank-model/profile.json', '{}')
    write(root / '.venv/Lib/site.py', 'print(1)\n')
    write(root / '.git/config', 'git')
    write(root / '.env', 'TOKEN=not-a-real-secret\n')
    write(root / 'README.md', 'readme\n')
    write(root / 'launcher.py', 'dev\n')
    write(root / '启动银行助手.py', 'dev\n')
    write(root / 'win_installer/build_installer.py', 'tool\n')
    return root


class PayloadTests(unittest.TestCase):
    def test_source_payload_includes_unknown_modules_and_excludes_dev_data(self):
        with TemporaryDirectory() as td:
            root = fixture_source(Path(td))
            entries = bi.source_payload(root)
            paths = {row['path'] for row in entries}
            self.assertEqual(entries, sorted(entries, key=lambda row: row['path']))
            self.assertIn('app/main.py', paths)
            self.assertIn('app/pending_view.py', paths)
            self.assertIn('app/pages/0_AI银行助手.py', paths)
            self.assertIn('future_pkg/mod.py', paths)
            self.assertIn('shared/contracts.py', paths)
            self.assertIn('.streamlit/config.toml', paths)
            for excluded in ('tests/test_app.py', 'docs/private.md', 'materials/secret.txt',
                             'runtime/sandbox.sqlite3', '.cache/bank-model/profile.json',
                             '.venv/Lib/site.py', '.git/config', '.env', 'README.md',
                             'launcher.py', '启动银行助手.py', 'win_installer/build_installer.py',
                             'requirements-app.txt'):
                self.assertNotIn(excluded, paths)
            self.assertTrue(all(row['size'] >= 0 and len(row['sha256']) == 64 for row in entries))

    def test_source_payload_rejects_bad_tree_and_links(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(bi.BuildError):
                bi.source_payload(root)
            write(root / 'app/main.py', 'x\n')
            bi.source_payload(root)
            with patch.object(bi, '_is_link', return_value=True):
                with self.assertRaises(bi.BuildError):
                    bi.source_payload(root)

    def test_high_confidence_secret_content_fails_closed(self):
        with TemporaryDirectory() as td:
            root = fixture_source(Path(td))
            bi.scan_secrets(root, bi.source_payload(root))
            write(root / 'app/leak.py', 'KEY = "' + 'sk-' + 'a' * 24 + '"\n')
            with self.assertRaises(bi.BuildError):
                bi.scan_secrets(root, bi.source_payload(root))
            write(root / 'app/leak.py', 'PRIVATE = "' + '-----BEGIN ' + 'RSA PRIVATE KEY-----"\n')
            with self.assertRaises(bi.BuildError):
                bi.scan_secrets(root, bi.source_payload(root))

    def test_manifest_is_strict_deterministic_and_content_bound(self):
        with TemporaryDirectory() as td:
            root = fixture_source(Path(td))
            entries = bi.source_payload(root)
            manifest = bi.build_payload_manifest(root, entries, version='1.0.0', brand='言行')
            self.assertEqual(manifest['brand'], '言行')
            self.assertEqual(manifest['version'], '1.0.0')
            self.assertEqual(manifest['file_count'], len(entries))
            self.assertEqual(manifest['total_bytes'], sum(row['size'] for row in entries))
            self.assertEqual(len(manifest['files']), len(entries))
            self.assertEqual(manifest['source_tree_sha256'],
                             bi.build_payload_manifest(root, bi.source_payload(root),
                                 version='1.0.0', brand='言行')['source_tree_sha256'])
            write(root / 'app/pending_view.py', 'PENDING = 2\n')
            changed = bi.build_payload_manifest(root, bi.source_payload(root),
                version='1.0.0', brand='言行')
            self.assertNotEqual(changed['source_tree_sha256'], manifest['source_tree_sha256'])
            with self.assertRaises(bi.BuildError):
                bi.build_payload_manifest(root, [dict(path='../escape.py', size=1, sha256='0' * 64)],
                    version='1.0.0', brand='言行')

    def test_licence_bundle_requires_evidence_and_keeps_sources(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            site = root / 'site-packages'
            first = site / 'alpha-1.0.dist-info'
            write(first / 'METADATA', 'Metadata-Version: 2.1\nName: alpha\nVersion: 1.0\n'
                  'License: MIT\nHome-page: https://example.invalid/alpha\n')
            write(first / 'LICENSE', 'MIT licence text\n')
            second = site / 'beta-2.0.dist-info'
            write(second / 'METADATA', 'Metadata-Version: 2.1\nName: beta\nVersion: 2.0\n'
                  'License-Expression: BSD-3-Clause\nProject-URL: Source, https://example.invalid/beta\n')
            out = root / 'licences'
            result = bi.license_bundle(site, out)
            names = {row['name'] for row in result['packages']}
            self.assertEqual(names, {'alpha', 'beta'})
            self.assertTrue(any('alpha' in row.lower() for row in result['files']))
            self.assertIn('alpha', result['notices'])
            self.assertIn('MIT', result['notices'])
            self.assertIn('BSD-3-Clause', result['notices'])
            self.assertIn('https://example.invalid/beta', result['notices'])
            gamma = site / 'gamma-3.0.dist-info'
            write(gamma / 'METADATA', 'Metadata-Version: 2.1\nName: gamma\nVersion: 3.0\n')
            with self.assertRaises(bi.BuildError) as seen:
                bi.license_bundle(site, out)
            self.assertIn('gamma', str(seen.exception))

    def test_removal_plan_preserves_data_by_default(self):
        manifest = dict(files=[
            dict(path='app/main.py', size=1, sha256='0' * 64),
            dict(path='python/python.exe', size=2, sha256='1' * 64),
            dict(path='docs/使用说明.txt', size=3, sha256='2' * 64)])
        default = bi.removal_plan(manifest)
        self.assertEqual(default['data_mode'], 'preserve')
        self.assertEqual(default['data_targets'], [])
        self.assertEqual([row['path'] for row in default['files']], ['app/main.py', 'python/python.exe', 'docs/使用说明.txt'])
        self.assertIn('app', default['directories'])
        self.assertIn('python', default['directories'])
        self.assertEqual(default['directories'], sorted(default['directories'], key=lambda value: (-value.count('/'), value)))
        cleared = bi.removal_plan(manifest, data_mode='clear')
        self.assertEqual(cleared['data_mode'], 'clear')
        self.assertIn('$LOCALAPPDATA\\言行\\data', cleared['data_targets'])
        self.assertIn('$INSTDIR\\runtime', cleared['data_targets'])
        self.assertIn('$INSTDIR\\.cache', cleared['data_targets'])
        self.assertTrue(cleared['preserve_unknown'])
        with self.assertRaises(bi.BuildError):
            bi.removal_plan(manifest, data_mode='guess')

    def test_nsis_script_is_chinese_strict_and_preserves_user_data(self):
        with TemporaryDirectory() as td:
            root = fixture_source(Path(td))
            entries = bi.source_payload(root)
            manifest = bi.build_payload_manifest(root, entries, version='1.0.0', brand='言行')
            context = dict(payload_dir=root / 'build' / 'payload',
                           output_path=root / 'dist' / '言行-1.0.0-x64-setup.exe',
                           manifest=manifest, version='1.0.0', brand='言行',
                           data_dir='$LOCALAPPDATA\\言行\\data',
                           payload_mb=bi.payload_mb(entries))
            script = bi.generate_nsis(context)
            for token in ('Unicode true', 'MUI_LANGUAGE "SimpChinese"',
                          'PRODUCT_VERSION "1.0.0"', 'VIProductVersion "1.0.0.0"',
                          'RequestExecutionLevel user',
                          'InstallDir "$LOCALAPPDATA\\Programs\\言行"',
                          '启动言行.lnk', '停止言行.lnk', '卸载言行.lnk',
                          'stop.py', '--check', '--quiet', '正在运行',
                          'app\\pending_view.py', 'python\\python.exe',
                          'WriteUninstaller', 'Uninstall\\言行',
                          'AvailableFreeSpace', 'RunningX64', 'AtLeastWin10',
                          'File /r'):
                if token == 'File /r':
                    self.assertNotIn(token, script)
                else:
                    self.assertIn(token, script)
            self.assertIn('MB_DEFBUTTON2', script)
            self.assertIn('同时删除', script)
            self.assertEqual(script.count('RMDir /r "$LOCALAPPDATA\\言行\\data"'), 1)
            self.assertIn('DisplayVersion', script)
            self.assertIn('1.0.0', script)
            self.assertIn('app/main.py'.replace('/', '\\'), script.replace('\\', '/').replace('/', '\\'))

    def test_tool_detection_and_artifact_verification_fail_closed(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            missing = root / 'missing-nsis' / 'makensis.exe'
            with self.assertRaises(bi.BuildError) as seen:
                bi.require_makensis(missing)
            self.assertIn('NSIS', str(seen.exception))
            fake = root / 'nsis' / 'makensis.exe'
            write(fake, b'MZfake')
            self.assertEqual(Path(bi.require_makensis(fake)), fake)
            artifact = root / 'setup.exe'
            payload = b'MZ' + b'x' * 2_000_000
            artifact.write_bytes(payload)
            manifest = dict(installer_sha256=hashlib.sha256(payload).hexdigest(),
                            installer_size=len(payload))
            result = bi.verify_artifact(artifact, manifest)
            self.assertEqual(result['size'], len(payload))
            with self.assertRaises(bi.BuildError):
                bi.verify_artifact(artifact, dict(manifest, installer_sha256='0' * 64))
            artifact.write_bytes(b'not-a-windows-executable')
            with self.assertRaises(bi.BuildError):
                bi.verify_artifact(artifact, dict(manifest, installer_sha256=hashlib.sha256(b'not-a-windows-executable').hexdigest(),
                                                  installer_size=25))
            with self.assertRaises(bi.BuildError):
                bi.verify_artifact(root / 'missing.exe', manifest)

    def test_compile_helper_rejects_tool_failure_and_empty_output(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            nsi = root / 'generated.nsi'
            nsi.write_text('; script', encoding='utf-8')
            artifact = root / 'out' / 'setup.exe'
            artifact.parent.mkdir(parents=True)
            failed = lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout='', stderr='compiler error')
            with self.assertRaises(bi.BuildError):
                bi.compile_installer(nsi, artifact, makensis=root / 'makensis.exe', runner=failed)
            self.assertFalse(artifact.exists())
            def empty(*args, **kwargs):
                artifact.write_bytes(b'')
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            with self.assertRaises(bi.BuildError):
                bi.compile_installer(nsi, artifact, makensis=root / 'makensis.exe', runner=empty)
            self.assertFalse(artifact.exists())
            def tiny(*args, **kwargs):
                artifact.write_bytes(b'MZ')
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            with self.assertRaises(bi.BuildError):
                bi.compile_installer(nsi, artifact, makensis=root / 'makensis.exe', runner=tiny)
            self.assertFalse(artifact.exists())

    def test_requirements_input_is_read_only_and_offline_safe(self):
        with TemporaryDirectory() as td:
            root = fixture_source(Path(td))
            lines = bi.read_requirements(root)
            self.assertEqual(lines, ['streamlit==1.65.0'])
            write(root / 'requirements-app.txt', '-e git+https://example.invalid/pkg\n')
            with self.assertRaises(bi.BuildError):
                bi.read_requirements(root)
            (root / 'requirements-app.txt').unlink()
            with self.assertRaises(bi.BuildError):
                bi.read_requirements(root)


REPO_ROOT = Path(bi.__file__).resolve().parents[1]


class ReleaseVersionTests(unittest.TestCase):
    """v2.0.0 交付入口一致性：版本、版本说明与发布载荷排除项。"""

    def test_release_version_is_2_0_0_everywhere_users_look(self):
        self.assertEqual(bi.PRODUCT_VERSION, '2.0.0')
        expected = {
            'README.md': ['2.0.0', '言行-2.0.0-x64-setup.exe'],
            'win_installer/README.md': ['2.0.0', 'dist/yanxing-v2.0.0'],
            'win_installer/payload_docs/使用说明.txt': ['言行 2.0.0'],
            'win_installer/payload_docs/版本与升级说明.txt': ['言行 2.0.0'],
            'docs/2026-10-28言行v2.0.0发布说明.md': ['2.0.0'],
            'docs/U1-U5程序内使用说明.md': ['2.0.0'],
        }
        for relative, needles in expected.items():
            path = REPO_ROOT / relative
            self.assertTrue(path.is_file(), relative)
            text = path.read_text(encoding='utf-8')
            for needle in needles:
                self.assertIn(needle, text, '%s 缺少 %r' % (relative, needle))

    def test_historical_reports_keep_their_original_version_labels(self):
        for relative, needle in (
                ('docs/2026-10-26安装包与GitHub发布验收.md', '1.0.0'),
                ('docs/2026-10-28全项目体验最终验收.md', '1.0.1'),
                ('docs/参赛材料/07-安装发布补充说明.md', '1.0.1')):
            text = (REPO_ROOT / relative).read_text(encoding='utf-8')
            self.assertIn(needle, text, relative)
            self.assertIn('历史', text, relative)

    def test_payload_never_ships_harness_probes_dev_data_or_git_pointer(self):
        entries = bi.source_payload(REPO_ROOT)
        paths = {row['path'] for row in entries}
        self.assertIn('app/main.py', paths)
        self.assertIn('app/visual.py', paths)
        self.assertIn('.streamlit/config.toml', paths)
        self.assertIn('materials/reviewed/2026-10-16/账单数据与手算_C_V1.3_复核修订.xlsx', paths)
        self.assertIn('materials/reviewed/2026-10-16/资金规则与功能复核_B_V1.2_复核修订.xlsx', paths)
        for forbidden in ('_vx_test_runner.py', '_vx_apptest_probe.py', '_vx_scratch_probe.py',
                          '.git', 'README.md', 'launcher.py', '启动银行助手.py',
                          '启动A演示.ps1', 'requirements-app.txt',
                          'win_installer/build_installer.py', 'local_reports/five-step-acceptance.json'):
            self.assertNotIn(forbidden, paths, forbidden)
        for prefix in ('tests/', 'docs/', 'runtime/', '.cache/', 'local_reports/',
                       'dist/', 'examples/', 'win_installer/'):
            self.assertFalse([row for row in paths if row.startswith(prefix)], prefix)
        self.assertIn('material_check/approved.py', paths)

    def test_probe_and_secret_fixtures_are_excluded_even_if_present(self):
        # `.git` 在 git worktree 中是文本指针；沙盒禁止在临时目录创建该名字，
        # 因此对常量做静态断言，并用真实前缀命名的探针验证同一分支。
        self.assertIn('.git', bi.EXCLUDED_ROOT_PREFIXES)
        with TemporaryDirectory() as td:
            root = fixture_source(Path(td))
            write(root / '_vx_test_runner.py', 'print(1)')
            write(root / '_vx_probe.py', 'print(1)')
            write(root / '.streamlit/secrets.toml', 'not-a-real-secret')
            entries = bi.source_payload(root)
            paths = {row['path'] for row in entries}
            for excluded in ('_vx_test_runner.py', '_vx_probe.py', '.streamlit/secrets.toml'):
                self.assertNotIn(excluded, paths)

    def test_license_page_states_current_version_and_upgrade_data_rule(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            python_license = root / 'LICENSE.txt'
            python_license.write_text('PSF licence', encoding='utf-8')
            result = dict(packages=[], files=[], notices='third-party notices')
            bi._license_page(root, result, python_license, version=bi.PRODUCT_VERSION)
            text = (root / '许可与第三方说明.txt').read_text(encoding='utf-8')
            self.assertIn('言行 2.0.0', text)
            self.assertNotIn('言行 1.0.0', text)
            self.assertIn('%LOCALAPPDATA%', text)
            self.assertIn('保留账号', text)


def __review_acceptance__():
    suite = unittest.TestSuite()
    seen = set()
    import gc
    for value in gc.get_objects():
        if not isinstance(value, type) or value is unittest.TestCase:
            continue
        try:
            if not issubclass(value, unittest.TestCase):
                continue
        except TypeError:
            continue
        module = getattr(value, '__module__', None)
        if module != '__main__' and module in sys.modules:
            continue
        if value in seen:
            continue
        seen.add(value)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(value))
    return suite


_main = sys.modules.get('__main__')
if _main is not None and not hasattr(_main, '__review_acceptance__'):
    _main.__review_acceptance__ = __review_acceptance__


if __name__ == '__main__':
    unittest.main(verbosity=2)
