"""Offline acceptance-tool and isolated phone-entry checks (no network, temp files only)."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import os
import subprocess
import sys
import json
from copy import deepcopy
from unittest.mock import patch

from agent.model_client import ModelError
from app.phone_acceptance import check_database, isolated_database
from scripts import evaluate_closeout_model as evaluator


class CloseoutToolTests(unittest.TestCase):
    def test_offline_acceptance_covers_current_trusted_flows(self):
        ledger_before = (evaluator.sha256_file(evaluator.LEDGER)
                         if evaluator.LEDGER.exists() else None)
        record = evaluator.run_offline()
        self.assertTrue(record['passed'])
        self.assertEqual(record['mode'], 'offline')
        self.assertTrue(record['simulated_model'])
        self.assertEqual(record['network_model_requests'], 0)
        self.assertEqual(len(record['cases']), len(evaluator.CASES))
        self.assertTrue(all(case['passed'] for case in record['cases']))
        self.assertEqual(set(record['six_scenarios']),
                         {'transfer', 'bill', 'wealth', 'card', 'subscription', 'birthday'})
        self.assertEqual(record['ledger_before'], None)
        self.assertIn('shared/contracts.py', record['source_sha256'])
        self.assertIn('scripts/evaluate_closeout_model.py', record['source_sha256'])
        self.assertIn('app/phone_acceptance.py', record['source_sha256'])
        self.assertEqual(record['tool_sha256'], evaluator.sha256_file(Path(evaluator.__file__)))
        self.assertEqual(ledger_before, evaluator.sha256_file(evaluator.LEDGER)
                         if evaluator.LEDGER.exists() else None)

    def test_ledger_cap_provider_and_no_reset(self):
        with TemporaryDirectory() as td:
            ledger = Path(td) / 'ledger.json'
            evaluator._write_json(ledger, dict(limit=20, used=19, provider='deepseek'))
            index, effective, value = evaluator.reserve_request(ledger)
            self.assertEqual((index, effective, value['used']), (20, 20, 20))
            with self.assertRaises(ModelError):
                evaluator.reserve_request(ledger)
            self.assertEqual(evaluator.read_ledger(ledger)['used'], 20)
            for bad in (dict(limit=21, used=0, provider='deepseek'),
                        dict(limit=0, used=0, provider='deepseek'),
                        dict(limit=20, used=0, provider='glm'),
                        dict(limit=20, used=21, provider='deepseek')):
                evaluator._write_json(ledger, bad)
                with self.assertRaises(ModelError):
                    evaluator.read_ledger(ledger)
            evaluator._write_json(ledger, dict(limit=5, used=4, provider='deepseek'))
            self.assertEqual(evaluator.reserve_request(ledger)[1], 5)

    def test_live_mode_refuses_before_network_without_ledger(self):
        with TemporaryDirectory() as td, patch.object(evaluator, 'LEDGER', Path(td) / 'missing.json'), patch.object(evaluator, 'EVIDENCE_DIR', Path(td)):
            output = evaluator.EVIDENCE_DIR / '2026-10-26-closeout-selfcheck-do-not-run.json'
            with self.assertRaises(ModelError):
                evaluator.run_live(suffix='selfcheck-do-not-run')
            self.assertFalse(output.exists())

    def test_single_birthday_plan_matches_page_expansion(self):
        build = evaluator.build_offline_replies
        def replies(name, *args):
            original = build(name, *args)
            if name == 'birthday':
                fields = deepcopy(original[0]['slots'])
                return [dict(reply='计划草稿', action='plan', tasks=[dict(id='birthday', title='生日计划', intent='birthday', slots=fields, depends_on=[])])]
            return original
        with patch.object(evaluator, 'build_offline_replies', side_effect=replies):
            result = evaluator.run_offline(['birthday'])
        self.assertTrue(result['passed'], result['cases'])

    def test_isolated_phone_database_rejects_project_and_runtime_paths(self):
        root = Path(evaluator.ROOT)
        with TemporaryDirectory() as td:
            database = isolated_database(root=root, temp_dir=Path(td))
            self.assertEqual(database.name, 'sandbox.sqlite3')
            self.assertTrue(database.parent.name.startswith('fintech-phone-acceptance-'))
            status = check_database(database)
            self.assertFalse(status['exists'])
            self.assertFalse(status['initialized'])
            self.assertIn('path', status)
        with self.assertRaises(RuntimeError):
            isolated_database(root=root, temp_dir=root)
        with self.assertRaises(RuntimeError):
            isolated_database(root=root, temp_dir=root / 'runtime')

    def test_phone_check_mode_is_read_only(self):
        with TemporaryDirectory() as td:
            database = isolated_database(root=Path(evaluator.ROOT), temp_dir=Path(td))
            self.assertFalse(database.exists())
            check_database(database)
            self.assertFalse(database.exists())
            from bank.seed import initialize
            from bank.store import Store
            initialize(Store(database))
            before = evaluator.sha256_file(database)
            env = os.environ.copy()
            env.update(TEMP=td, TMP=td)
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(Path(evaluator.ROOT) / 'app/phone_acceptance.py'), '--check'], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0)
            status = json.loads(result.stdout)
            self.assertNotIn('error', status)
            self.assertFalse(status['initialized'])
            self.assertEqual(evaluator.sha256_file(database), before)

    def test_phone_page_reruns_and_keeps_imported_aliases_isolated(self):
        from streamlit.testing.v1 import AppTest
        from app import auth_ui, demo, ui
        from bank import seed
        from bank.store import Store
        with TemporaryDirectory() as td, patch('tempfile.gettempdir', return_value=td), patch.object(seed, 'demo_path', seed.demo_path), patch.object(auth_ui, 'demo_path', auth_ui.demo_path), patch.object(demo, 'demo_path', demo.demo_path), patch.object(auth_ui, 'gate', return_value=None), patch.object(ui, '_NAV_LOOKUP', deepcopy(ui._NAV_LOOKUP)):
            page = AppTest.from_file(str(Path(evaluator.ROOT) / 'app/phone_acceptance.py'), default_timeout=20).run()
            self.assertFalse(page.exception)
            first = len(page.title) + len(page.header) + len(page.button)
            self.assertGreater(first, 0)
            database = isolated_database(temp_dir=Path(td))
            self.assertEqual(seed.demo_path(), database)
            self.assertEqual(auth_ui.demo_path(), database)
            self.assertEqual(demo.demo_path(), database)
            page.run()
            self.assertFalse(page.exception)
            self.assertGreater(len(page.title) + len(page.header) + len(page.button), 0)
            page.button(key='ui_menu_transfer').click().run()
            self.assertFalse(page.exception)
            page.button(key='ui_menu_home').click().run()
            self.assertFalse(page.exception)
            with Store(database).connection() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM auth_users').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
