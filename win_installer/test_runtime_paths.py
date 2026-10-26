"""Independent regressions for OS virtual paths and the user's shortcut rule."""
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from . import build_installer as bi
from .test_installer import fixture_source


class RuntimePathTests(unittest.TestCase):
    def test_os_virtualized_data_path_is_canonical_and_raw_junction_is_rejected(self):
        with TemporaryDirectory() as td:
            base = Path(td).resolve()
            raw = base / 'os-local' / '言行' / 'data'
            canonical = base / 'os-private-cache' / '言行' / 'data'
            original = Path.resolve

            def virtual_resolve(path, *args, **kwargs):
                return canonical if path == raw else original(path, *args, **kwargs)

            source = Path(__file__).parent / 'runtime' / 'paths.py'
            spec = importlib.util.spec_from_file_location('installer_paths_test', source)
            module = importlib.util.module_from_spec(spec)
            with patch.dict(os.environ, LOCALAPPDATA=str(base / 'os-local')), patch.object(Path, 'resolve', virtual_resolve):
                spec.loader.exec_module(module)
                self.assertEqual(module.safe_data(), canonical)
                with patch.object(Path, 'is_junction', lambda path: path == raw):
                    with self.assertRaises(RuntimeError):
                        module.safe_data()

    def test_source_junction_is_a_link_even_when_not_a_symbolic_link(self):
        with patch.object(Path, 'is_symlink', return_value=False), patch.object(Path, 'is_junction', return_value=True):
            self.assertTrue(bi._is_link(Path('untrusted-source-directory')))

    def test_generated_shortcuts_only_use_desktop_other_and_preserve_shared_folder(self):
        with TemporaryDirectory() as td:
            root = fixture_source(Path(td))
            manifest = bi.build_payload_manifest(root, bi.source_payload(root))
            script = bi.generate_nsis(dict(manifest=manifest, version='1.0.0', brand='言行',
                payload_dir=root / 'payload', output_path=root / 'setup.exe'))
            created = [line for line in script.splitlines() if line.strip().startswith('CreateShortCut ')]
            self.assertEqual(len(created), 3)
            self.assertTrue(all('"$DESKTOP\\other\\' in line for line in created))
            self.assertNotIn('$SMPROGRAMS', script)
            self.assertNotIn('RMDir "$DESKTOP\\other"', script)
            self.assertIn('"-B $\\"$INSTDIR\\launcher\\launch.py', script)


if __name__ == '__main__':
    unittest.main()
