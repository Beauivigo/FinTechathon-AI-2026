"""模型传输和本机凭证边界的独立验收，不连接真实模型。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from agent.model_client import ModelClient, ModelConfig, ModelError, endpoint, load_config
from agent.model_credentials import CredentialStore, CredentialError


class Reply:
    def __init__(self, raw): self.raw=raw
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self, limit): return self.raw[:limit]


class FakeHTTP:
    def __init__(self, value): self.value=value; self.calls=[]
    def open(self, req, timeout):
        self.calls.append((req,timeout))
        if isinstance(self.value, Exception): raise self.value
        return Reply(self.value)


def completion(content='{"intent":"balance"}', **kwargs):
    value={'model':'deepseek-flash','choices':[{'message':{'content':content},'finish_reason':'stop'}],
           'usage':{'total_tokens':10,'completion_tokens':3,'prompt_tokens':7}}
    value.update(kwargs)
    return json.dumps(value).encode()


class ModelTransportTests(unittest.TestCase):
    def setUp(self):
        self.key='test-key-for-offline-transport'
        self.config=ModelConfig(api_key=self.key,source='configured')
        self.messages=[{'role':'system','content':'Return json only.'},{'role':'user','content':'查余额'}]

    def test_real_protocol_shape_and_no_secret_in_metadata_or_repr(self):
        http=FakeHTTP(completion())
        client=ModelClient(self.config,opener=http)
        self.assertEqual(json.loads(client(self.messages)),{'intent':'balance'})
        req,timeout=http.calls[0]
        body=json.loads(req.data)
        self.assertEqual(req.full_url,'https://api.deepseek.com/chat/completions')
        self.assertEqual(body['messages'],self.messages)
        self.assertEqual(body['response_format'],{'type':'json_object'})
        self.assertEqual(body['thinking'],{'type':'disabled'})
        self.assertFalse(body['stream'])
        self.assertEqual(timeout,45)
        self.assertNotIn(self.key,repr(self.config)+json.dumps(client.last_metadata)+req.data.decode())

    def test_rejects_remote_redirect_targets_and_userinfo(self):
        for address in ['https://evil.example','http://api.deepseek.com','https://api.deepseek.com@evil.example',
                        'https://api.deepseek.com/path','https://api.deepseek.com?x=1','http://localhost.evil:11434/v1']:
            with self.subTest(address=address),self.assertRaises(ModelError):endpoint(address)
        self.assertEqual(endpoint('http://127.0.0.1:11434/v1')[1],False)

    def test_invalid_output_never_retries_or_returns_partial_result(self):
        bad=[completion('{"intent":"balance","intent":"transfer"}'),completion('{"value":NaN}'),completion(''),
             completion(model='wrong-model'),b'{"choices":[]}',b'not json']
        for raw in bad:
            with self.subTest(raw=raw):
                http=FakeHTTP(raw)
                with self.assertRaises(ModelError):ModelClient(self.config,opener=http)(self.messages)
                self.assertEqual(len(http.calls),1)

    def test_transport_errors_are_sanitized_and_no_automatic_retry(self):
        http=FakeHTTP(HTTPError('https://api.deepseek.com',429,self.key,{},None))
        with self.assertRaises(ModelError) as seen:ModelClient(self.config,opener=http)(self.messages)
        self.assertNotIn(self.key,str(seen.exception))
        self.assertEqual(len(http.calls),1)

    def test_invalid_messages_do_not_send(self):
        http=FakeHTTP(completion())
        for value in [[],[{'role':'tool','content':'x'}],[{'role':'user','content':'x','confirmed':True}]]:
            with self.assertRaises(ModelError):ModelClient(self.config,opener=http)(value)
        self.assertFalse(http.calls)

    def test_native_store_encrypts_switches_updates_and_clears(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'profile.json'
            store=CredentialStore(path)
            store.save(mode='independent',key=self.key)
            self.assertNotIn(self.key,path.read_text())
            self.assertEqual(store.key(),self.key)
            store.save(mode='desktop')
            self.assertEqual(store.key(),'')
            store.save(mode='independent',key='another-offline-key-value')
            self.assertEqual(store.key(),'another-offline-key-value')
            store.save(mode='missing',clear=True)
            self.assertNotIn('encrypted_key',store.read())

    def test_config_does_not_extract_desktop_key(self):
        with tempfile.TemporaryDirectory() as td,patch('agent.model_client.CredentialStore',return_value=CredentialStore(Path(td)/'profile.json')),patch('agent.model_client.desktop_key') as read_key,patch.dict('os.environ',{},clear=True):
            self.assertEqual(load_config().source,'missing')
            self.assertEqual(load_config({'BANK_MODEL_USE_DESKTOP_PET':True}).source,'desktop')
            read_key.assert_not_called()

    def test_bad_key_does_not_replace_saved_profile(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'profile.json'
            store=CredentialStore(path)
            store.save(mode='desktop')
            before=path.read_bytes()
            with self.assertRaises(CredentialError):store.save(mode='independent',key='bad')
            self.assertEqual(path.read_bytes(),before)

    def test_local_endpoint_never_inherits_official_provider_key(self):
        with tempfile.TemporaryDirectory() as td,patch('agent.model_client.CredentialStore',return_value=CredentialStore(Path(td)/'profile.json')),patch.dict('os.environ',{'BANK_MODEL_BASE_URL':'http://127.0.0.1:11434/v1','DEEPSEEK_API_KEY':self.key},clear=True):
            config=load_config()
            self.assertEqual(config.source,'local');self.assertEqual(config.api_key,'')
