"""真实Streamlit设置页：保存/修改/切换/清除，明文不回显或落盘。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from agent.model_credentials import CredentialStore


class ModelSettingsAcceptance(unittest.TestCase):
    def test_save_modify_and_clear_key_in_real_page(self):
        with tempfile.TemporaryDirectory() as td:
            store=CredentialStore(Path(td)/'profile.json')
            with patch('app.model_settings.CredentialStore',return_value=store):
                app=AppTest.from_string('from app.model_settings import render_settings\nrender_settings()').run()
                self.assertFalse(app.exception)
                secret='offline-settings-first-key'
                app.text_input(key='bank_model_new_key').set_value(secret)
                app.button(key='bank_model_save').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(store.key(),secret)
                self.assertEqual(app.text_input(key='bank_model_new_key').value,'')
                self.assertNotIn(secret,store.path.read_text())
                changed='offline-settings-second-key'
                app.text_input(key='bank_model_new_key').set_value(changed)
                app.button(key='bank_model_save').click().run()
                self.assertEqual(store.key(),changed)
                self.assertEqual(app.text_input(key='bank_model_new_key').value,'')
                app.button(key='bank_model_desktop').click().run()
                self.assertEqual(store.read()['mode'],'desktop')
                self.assertEqual(store.key(),'')
                app.button(key='bank_model_key_clear').click().run()
                self.assertEqual(store.read()['mode'],'missing')
                self.assertNotIn('encrypted_key',store.read())
                self.assertFalse(app.exception)
