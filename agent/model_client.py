"""受控的国内模型JSON传输；固定官方目标，不持久化明文凭证。"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
from urllib import error, request
from urllib.parse import urlsplit

from shared.adapters import strict_json_loads
from agent.model_credentials import CredentialStore, CredentialError
from agent.model_providers import PROVIDERS, valid_model

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = Path('D:/Desktop Pet/gpt project')
OFFICIAL = 'https://api.deepseek.com'
MAX_RESPONSE = 262144


class ModelError(ValueError):
    """只含可向用户显示的错误；不携带服务端原文或凭证。"""


def wire_model(name):
    return 'deepseek-flash' if name in {'deepseek-v4-flash', 'deepseek-v4-flash-vision-exp'} else name


def endpoint(base):
    _, official = endpoint_provider(base)
    return base.rstrip('/') + '/chat/completions', official


def endpoint_provider(base):
    try:
        if not isinstance(base, str) or any(c.isspace() for c in base):
            raise ValueError()
        parts = urlsplit(base)
        port = parts.port
        if parts.username is not None or parts.password is not None or '?' in base or '#' in base:
            raise ValueError()
        if parts.scheme == 'https' and port in (None, 443):
            for name, provider in PROVIDERS.items():
                allowed = urlsplit(provider.base_url)
                paths = {'', '/v1'} if name == 'deepseek' else {allowed.path}
                if parts.hostname == allowed.hostname and parts.path.rstrip('/') in paths:
                    return name, True
        if parts.scheme == 'http' and parts.hostname in {'127.0.0.1', '::1'} and port != 0 and parts.path.rstrip('/') in {'', '/v1'}:
            return 'local', False
    except (ValueError, TypeError):
        pass
    raise ModelError('模型地址只能使用已支持服务商的官方接口或本机回环地址。')


def desktop_settings():
    folder = Path(os.environ.get('APPDATA', '')) / 'Desktop Pet'
    try:
        settings = json.loads((folder / 'settings.json').read_text(encoding='utf-8')).get('deepSeek', {})
        model = settings.get('model', 'deepseek-flash')
        return folder, wire_model(model) if isinstance(model, str) else 'deepseek-flash'
    except (OSError, ValueError, AttributeError):
        return folder, 'deepseek-flash'


def desktop_key():
    """通过已安装 Electron 解密桌宠凭证；只捕获到当前进程内存。"""
    folder, _ = desktop_settings()
    executable = DESKTOP_ROOT / 'node_modules/electron/dist/electron.exe'
    helper = ROOT / 'scripts/desktop_pet_model_key.cjs'
    if not all(p.is_file() for p in [executable, helper, folder / 'deepseek-credentials.json', folder / 'Local State']):
        raise ModelError('桌宠本机配置不可用，请配置 BANK_MODEL_API_KEY 或页面中的模型密钥。')
    child_env = dict(os.environ)
    child_env.pop('ELECTRON_RUN_AS_NODE', None)
    try:
        result = subprocess.run([str(executable), str(helper), str(folder)], capture_output=True,
            timeout=20, env=child_env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ModelError('无法读取桌宠本机凭证，请在模型设置中单独配置密钥。')
        value = json.loads(result.stdout.decode('utf-8'))
        key = value.get('key')
        if not isinstance(key, str) or not 12 <= len(key) <= 512 or any(c.isspace() for c in key):
            raise ModelError('桌宠本机凭证格式无效。')
        return key
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError) as exc:
        if isinstance(exc, ModelError):
            raise
        raise ModelError('无法读取桌宠本机凭证，请在模型设置中单独配置密钥。') from None


@dataclass(repr=False)
class ModelConfig:
    model: str = 'deepseek-flash'
    base_url: str = OFFICIAL
    api_key: str = field(default='', repr=False)
    source: str = 'missing'
    timeout: int = 45
    provider: str = ''

    def __repr__(self):
        return f'ModelConfig(model={self.model!r}, source={self.source!r})'


def load_config(secrets=None):
    values = secrets or {}
    try:
        store = CredentialStore()
        profile = store.read()
    except CredentialError as exc:
        raise ModelError(str(exc)) from None
    def setting(name):
        return os.environ.get(name) or values.get(name)
    selected = setting('BANK_MODEL_PROVIDER')
    base = setting('BANK_MODEL_BASE_URL')
    generic_key = setting('BANK_MODEL_API_KEY')
    if selected is not None and selected not in {*PROVIDERS, 'local'}:
        raise ModelError('模型服务商无效。')
    if base:
        provider, official = endpoint_provider(base)
        if selected and selected != provider:
            raise ModelError('模型服务商与接口地址不匹配，未发送请求。')
    else:
        # 旧服务器配置中的通用密钥仍绑定DeepSeek，不随页面选择换发给别家。
        provider = selected or ('deepseek' if generic_key else profile['provider'])
        if provider == 'local':
            raise ModelError('本地模型需要配置BANK_MODEL_BASE_URL回环地址。')
        base, official = PROVIDERS[provider].base_url, True
    key = generic_key or ''
    if official and not key:
        key = next((setting(name) for name in PROVIDERS[provider].key_envs if setting(name)), '')
    matching_profile = official and profile['provider'] == provider
    try:
        saved_key = store.key() if matching_profile and not key else ''
    except CredentialError as exc:
        raise ModelError(str(exc)) from None
    key = key or saved_key
    desktop = provider == 'deepseek' and (os.environ.get('BANK_MODEL_USE_DESKTOP_PET') == '1' or values.get('BANK_MODEL_USE_DESKTOP_PET') is True or (matching_profile and profile['mode'] == 'desktop'))
    if matching_profile and profile['mode'] == 'independent':
        default_model = profile['model']
    elif provider == 'deepseek':
        _, default_model = desktop_settings()
    else:
        default_model = PROVIDERS[provider].models[0] if official else 'local-model'
    model = setting('BANK_MODEL_NAME') or default_model
    if not valid_model(model):
        raise ModelError('模型名称无效。')
    source = 'configured' if key else ('desktop' if desktop else ('missing' if official else 'local'))
    return ModelConfig(wire_model(model) if provider == 'deepseek' else model, base, key, source, provider=provider)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ModelClient:
    """messages -> JSON文本；无工具调用、无银行执行、无自动付费重试。"""
    def __init__(self, config=None, *, opener=None):
        self.config = config or load_config()
        self._opener = opener or request.build_opener(_NoRedirect())
        self.last_metadata = {}

    def __call__(self, messages):
        self.last_metadata = {}
        url, official = endpoint(self.config.base_url)
        provider, _ = endpoint_provider(self.config.base_url)
        if self.config.provider and self.config.provider != provider:
            raise ModelError('模型服务商与接口地址不匹配，未发送请求。')
        if not valid_model(self.config.model):
            raise ModelError('模型名称无效。')
        if not isinstance(messages, list) or not 1 <= len(messages) <= 40:
            raise ModelError('对话上下文过长，请开始新的任务。')
        if any(not isinstance(m, dict) or set(m) != {'role', 'content'} or m['role'] not in {'system', 'user', 'assistant'} or not isinstance(m['content'], str) for m in messages):
            raise ModelError('模型消息格式无效。')
        if self.config.source == 'desktop' and provider != 'deepseek':
            raise ModelError('桌宠凭证只支持DeepSeek。')
        key = self.config.api_key or (desktop_key() if provider == 'deepseek' and self.config.source == 'desktop' else '')
        if (official or key) and (not isinstance(key, str) or not key.isascii() or not 12 <= len(key) <= 512 or any(c.isspace() for c in key)):
            raise ModelError('请先配置模型密钥。')
        body = dict(model=self.config.model, messages=messages, stream=False,
            response_format={'type': 'json_object'}, max_tokens=4096)
        model = self.config.model
        if provider == 'kimi' and model.startswith(('kimi-k3', 'kimi-k2.7')):
            raise ModelError('当前银行对话尚不支持需回传思考内容的Kimi模型，请使用kimi-k2.6。')
        disable_thinking = provider in {'deepseek', 'doubao'}
        if provider == 'glm':
            disable_thinking = model.startswith(('glm-4.5', 'glm-4.6', 'glm-4.7', 'glm-5.1', 'glm-5.2')) or model in {'glm-5', 'glm-5-turbo'}
        elif provider == 'kimi':
            disable_thinking = model.startswith(('kimi-k2.5', 'kimi-k2.6'))
        if disable_thinking:
            body['thinking'] = {'type': 'disabled'}
        elif provider == 'qwen':
            body['enable_thinking'] = False
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        if len(payload) > 65536:
            raise ModelError('对话上下文过长，请开始新的任务。')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if key:
            headers['Authorization'] = 'Bearer ' + key
        req = request.Request(url, data=payload, headers=headers, method='POST')
        try:
            with self._opener.open(req, timeout=self.config.timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except error.HTTPError as exc:
            messages_by_status = {401:'模型密钥无效，请重新配置。', 402:'模型账户余额不足。', 429:'模型服务限流，请稍后手动重试。'}
            raise ModelError(messages_by_status.get(exc.code, '模型服务未完成请求，请稍后手动重试。')) from None
        except (OSError, error.URLError, TimeoutError):
            raise ModelError('模型连接超时或不可用，本次未生成新计划。') from None
        if len(raw) > MAX_RESPONSE:
            raise ModelError('模型回复过长，本次未生成新计划。')
        try:
            value = strict_json_loads(raw.decode('utf-8'))
            choice = value['choices'][0]
            text = choice['message']['content']
            actual = value.get('model', '')
            if choice.get('finish_reason') != 'stop' or choice['message'].get('tool_calls'):
                raise ValueError()
            if not isinstance(text, str) or not text.strip() or (official and not valid_model(actual)) or (provider == 'deepseek' and wire_model(actual) != wire_model(self.config.model)):
                raise ValueError()
            if not isinstance(strict_json_loads(text), dict):
                raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError, UnicodeError):
            raise ModelError('模型未返回完整有效的JSON计划，本次不执行。') from None
        usage = value.get('usage', {})
        if not isinstance(usage, dict):
            usage = {}
        self.last_metadata = {'model': actual, 'provider': provider, 'credential_source': self.config.source,
            'usage': {k:v for k,v in usage.items() if k in {'prompt_tokens','completion_tokens','total_tokens'} and type(v) is int and v >= 0}}
        return text
