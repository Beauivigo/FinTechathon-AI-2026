"""真实Streamlit设置页：保存/修改/切换/清除，明文不回显或落盘。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from agent.model_credentials import CredentialStore


class ModelSettingsAcceptance(unittest.TestCase):
    def test_provider_switch_model_id_and_matching_config_on_real_page(self):
        from agent.model_client import load_config
        from agent.model_providers import PROVIDERS
        env_names = ['BANK_MODEL_PROVIDER', 'BANK_MODEL_API_KEY', 'BANK_MODEL_BASE_URL', 'BANK_MODEL_NAME', 'BANK_MODEL_USE_DESKTOP_PET']
        env_names += [name for spec in PROVIDERS.values() for name in spec.key_envs]
        with tempfile.TemporaryDirectory() as td:
            store = CredentialStore(Path(td)/'profile.json')
            with patch('app.model_settings.CredentialStore', return_value=store), patch('agent.model_client.CredentialStore', return_value=store), patch.dict('os.environ', dict.fromkeys(env_names, '')):
                app = AppTest.from_string('from app.model_settings import render_settings\nrender_settings()').run(timeout=30)
                self.assertEqual(len(app.selectbox(key='bank_model_provider').options), 5)
                app.text_input(key='bank_model_new_key').set_value('offline-unsaved-deepseek-key').run()
                for provider, spec in PROVIDERS.items():
                    if provider == 'deepseek':
                        continue
                    app.selectbox(key='bank_model_provider').set_value(provider).run()
                    self.assertFalse(app.exception)
                    self.assertEqual(app.text_input(key='bank_model_new_key').value, '')
                    self.assertEqual(app.selectbox(key='bank_model_choice').value, spec.models[0])
                    self.assertEqual(app.button(key='bank_model_desktop').disabled, provider != 'deepseek')
                    previous = store.path.read_bytes() if store.path.exists() else None
                    app.button(key='bank_model_save').click().run()
                    self.assertEqual(store.path.read_bytes() if store.path.exists() else None, previous)
                    app.text_input(key='bank_model_new_key').set_value('offline-page-key-for-' + provider)
                    app.button(key='bank_model_save').click().run()
                    self.assertFalse(app.exception)
                    self.assertEqual(store.read()['provider'], provider)
                    self.assertEqual(load_config().provider, provider)
                    self.assertEqual(load_config().base_url, spec.base_url)
                    self.assertEqual(app.text_input(key='bank_model_new_key').value, '')
                app.selectbox(key='bank_model_provider').set_value('doubao').run()
                app.text_input(key='bank_model_custom').set_value('ep-offline-page-endpoint')
                app.text_input(key='bank_model_new_key').set_value('offline-page-doubao-key')
                app.button(key='bank_model_save').click().run()
                self.assertEqual(store.read()['model'], 'ep-offline-page-endpoint')
                self.assertEqual(load_config().model, 'ep-offline-page-endpoint')
                app.button(key='bank_model_key_clear').click().run()
                self.assertEqual(load_config().source, 'missing')
                self.assertFalse(app.exception)

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
