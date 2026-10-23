"""多服务商的独立离线验收：真实请求装配、密钥边界与迁移。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from agent.model_client import ModelClient, ModelConfig, ModelError, endpoint, load_config
from agent.model_credentials import CredentialStore, CredentialError
from agent.model_providers import PROVIDERS
from tests.test_model_transport import FakeHTTP, completion


class ProviderAcceptance(unittest.TestCase):
    key = 'offline-provider-specific-key'
    messages = [{'role': 'system', 'content': 'Return JSON.'}, {'role': 'user', 'content': '查余额'}]

    def test_every_preset_builds_the_correct_request_without_extra_provider_fields(self):
        for provider, spec in PROVIDERS.items():
            for model in spec.models:
                with self.subTest(provider=provider, model=model):
                    http = FakeHTTP(completion(model=model))
                    client = ModelClient(ModelConfig(model, spec.base_url, self.key, 'configured', provider=provider), opener=http)
                    self.assertEqual(json.loads(client(self.messages)), {'intent': 'balance'})
                    req = http.calls[0][0]
                    body = json.loads(req.data)
                    self.assertEqual(req.full_url, spec.base_url + '/chat/completions')
                    self.assertEqual(req.get_header('Authorization'), 'Bearer ' + self.key)
                    self.assertEqual(body['response_format'], {'type': 'json_object'})
                    self.assertEqual(body['model'], model)
                    if provider == 'qwen':
                        self.assertIs(body['enable_thinking'], False)
                        self.assertNotIn('thinking', body)
                    else:
                        self.assertEqual(body['thinking'], {'type': 'disabled'})
                        self.assertNotIn('enable_thinking', body)
                    self.assertEqual(client.last_metadata['provider'], provider)
                    self.assertNotIn(self.key, repr(client.config) + json.dumps(client.last_metadata) + req.data.decode())

    def test_new_official_hosts_still_reject_spoofs_wrong_paths_and_redirects(self):
        for spec in PROVIDERS.values():
            for url in [spec.base_url.replace('https://', 'http://'), spec.base_url + '/other',
                        spec.base_url.replace('https://', 'https://@'), spec.base_url + '?', spec.base_url + '#',
                        spec.base_url.replace('.cn', '.cn.evil.example') if '.cn' in spec.base_url else spec.base_url.replace('.com', '.com.evil.example')]:
                with self.subTest(url=url), self.assertRaises(ModelError):
                    endpoint(url)
            http = FakeHTTP(HTTPError(spec.base_url, 302, self.key, {}, None))
            with self.assertRaises(ModelError) as seen:
                ModelClient(ModelConfig(spec.models[0], spec.base_url, self.key), opener=http)(self.messages)
            self.assertEqual(len(http.calls), 1)
            self.assertNotIn(self.key, str(seen.exception))
        for url in ['https://open.bigmodel.cn:bad/api/paas/v4', 'https://open.bigmodel.cn:444/api/paas/v4',
                    'http://127.0.0.1:0/v1', None, 'https://open.bigmodel.cn\n/api/paas/v4']:
            with self.subTest(url=url), self.assertRaises(ModelError):
                endpoint(url)

    def test_provider_mismatch_or_desktop_source_never_sends(self):
        spec = PROVIDERS['glm']
        for config in [ModelConfig('glm-4.7', spec.base_url, self.key, provider='deepseek'),
                       ModelConfig('glm-4.7', spec.base_url, source='desktop')]:
            http = FakeHTTP(completion())
            with patch('agent.model_client.desktop_key') as desktop_key, self.assertRaises(ModelError):
                ModelClient(config, opener=http)(self.messages)
            desktop_key.assert_not_called()
            self.assertFalse(http.calls)

    def test_environment_and_saved_keys_are_scoped_to_the_selected_provider(self):
        with tempfile.TemporaryDirectory() as td:
            store = CredentialStore(Path(td) / 'profile.json')
            store.save(mode='independent', key=self.key)
            for provider, spec in PROVIDERS.items():
                if provider == 'deepseek':
                    continue
                with self.subTest(provider=provider), patch('agent.model_client.CredentialStore', return_value=store), patch.dict('os.environ',
                        {'BANK_MODEL_PROVIDER': provider, 'DEEPSEEK_API_KEY': self.key, 'BANK_MODEL_USE_DESKTOP_PET': '1'}, clear=True):
                    config = load_config()
                    self.assertEqual(config.provider, provider)
                    self.assertEqual(config.base_url, spec.base_url)
                    self.assertEqual(config.model, spec.models[0])
                    self.assertEqual(config.api_key, '')
                    self.assertEqual(config.source, 'missing')
                with patch('agent.model_client.CredentialStore', return_value=store), patch.dict('os.environ',
                        {'BANK_MODEL_PROVIDER': provider, spec.key_envs[0]: 'offline-other-provider-key'}, clear=True):
                    config = load_config()
                    self.assertEqual(config.api_key, 'offline-other-provider-key')
            store.save(mode='independent', provider='glm', model='glm-4.7', key='offline-glm-saved-key')
            with patch('agent.model_client.CredentialStore', return_value=store), patch.dict('os.environ', {}, clear=True):
                config = load_config()
                self.assertEqual((config.provider, config.model, config.api_key), ('glm', 'glm-4.7', 'offline-glm-saved-key'))
            with patch('agent.model_client.CredentialStore', return_value=store), patch.dict('os.environ', {'BANK_MODEL_API_KEY': self.key}, clear=True):
                config = load_config()
                self.assertEqual(config.provider, 'deepseek')
                self.assertEqual(config.api_key, self.key)

    def test_explicit_server_configuration_and_mismatch(self):
        with tempfile.TemporaryDirectory() as td, patch('agent.model_client.CredentialStore', return_value=CredentialStore(Path(td)/'profile.json')), patch.dict('os.environ', {}, clear=True):
            config = load_config({'BANK_MODEL_PROVIDER': 'qwen', 'BANK_MODEL_API_KEY': self.key, 'BANK_MODEL_NAME': 'qwen-plus'})
            self.assertEqual((config.provider, config.model, config.api_key), ('qwen', 'qwen-plus', self.key))
            config = load_config({'BANK_MODEL_BASE_URL': PROVIDERS['glm'].base_url, 'ZHIPU_API_KEY': self.key})
            self.assertEqual((config.provider, config.api_key), ('glm', self.key))
            for settings in [{'BANK_MODEL_PROVIDER': 'unknown'}, {'BANK_MODEL_PROVIDER': 'local'},
                             {'BANK_MODEL_PROVIDER': 'glm', 'BANK_MODEL_BASE_URL': PROVIDERS['qwen'].base_url}]:
                with self.subTest(settings=settings), self.assertRaises(ModelError):
                    load_config(settings)
            with patch('agent.model_client.desktop_settings', return_value=(Path(td), 'deepseek-v4-pro')):
                self.assertEqual(load_config({'BANK_MODEL_API_KEY': self.key}).model, 'deepseek-v4-pro')
                CredentialStore(Path(td)/'profile.json').save(mode='independent', model='deepseek-flash', key=self.key)
                self.assertEqual(load_config({'BANK_MODEL_USE_DESKTOP_PET': True}).model, 'deepseek-flash')

    def test_old_encrypted_profile_migrates_without_rewriting_and_switch_requires_new_key(self):
        with tempfile.TemporaryDirectory() as td:
            store = CredentialStore(Path(td) / 'profile.json')
            store.save(mode='independent', key=self.key)
            legacy = store.read()
            legacy['version'] = 1
            legacy.pop('provider')
            store.path.write_text(json.dumps(legacy), encoding='utf-8')
            before = store.path.read_bytes()
            self.assertEqual(store.read()['provider'], 'deepseek')
            self.assertEqual(store.key(), self.key)
            self.assertEqual(store.path.read_bytes(), before)
            with self.assertRaises(CredentialError):
                store.save(mode='independent', provider='glm', model='glm-4.7')
            self.assertEqual(store.path.read_bytes(), before)
            store.save(mode='independent', provider='glm', model='glm-4.7', key='offline-new-glm-key')
            self.assertEqual(store.key(), 'offline-new-glm-key')
            self.assertNotIn('offline-new-glm-key', store.path.read_text())
            store.save(mode='independent', provider='glm', model='glm-5.2')
            self.assertEqual(store.read()['model'], 'glm-5.2')
            self.assertEqual(store.key(), 'offline-new-glm-key')
            with self.assertRaises(CredentialError):
                store.save(mode='desktop', provider='glm', model='glm-4.7')
            store.save(mode='missing', provider='glm', model='glm-4.7', clear=True)
            self.assertEqual(store.key(), '')
            self.assertNotIn('encrypted_key', store.read())

    def test_cloud_alias_and_doubao_endpoint_id_preserve_actual_model_metadata(self):
        for provider, model, actual in [('qwen', 'qwen-plus', 'qwen-plus-2026-09-28'),
                                        ('doubao', 'ep-offline-endpoint', 'doubao-seed-2-1-pro-260915')]:
            client = ModelClient(ModelConfig(model, PROVIDERS[provider].base_url, self.key), opener=FakeHTTP(completion(model=actual)))
            self.assertEqual(json.loads(client(self.messages)), {'intent': 'balance'})
            self.assertEqual(client.last_metadata['model'], actual)

    def test_invalid_json_and_truncation_never_become_a_plan_for_any_provider(self):
        for provider, spec in PROVIDERS.items():
            for raw in [completion('[]', model=spec.models[0]), completion('{"intent":"balance"}', model=spec.models[0],
                    choices=[{'message': {'content': '{"intent":"balance"}'}, 'finish_reason': 'length'}])]:
                http = FakeHTTP(raw)
                with self.subTest(provider=provider), self.assertRaises(ModelError):
                    ModelClient(ModelConfig(spec.models[0], spec.base_url, self.key), opener=http)(self.messages)
                self.assertEqual(len(http.calls), 1)

    def test_unsupported_preserved_thinking_models_stop_before_request(self):
        for model in ['kimi-k3', 'kimi-k2.7-code']:
            http = FakeHTTP(completion())
            with self.assertRaises(ModelError):
                ModelClient(ModelConfig(model, PROVIDERS['kimi'].base_url, self.key), opener=http)(self.messages)
            self.assertFalse(http.calls)
