"""受控模型传输；复用桌宠的 DeepSeek JSON 协议，不持久化明文凭证。"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
from urllib import error, request
from urllib.parse import urlsplit

from shared.adapters import strict_json_loads
from agent.model_credentials import CredentialStore, CredentialError

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = Path('D:/Desktop Pet/gpt project')
OFFICIAL = 'https://api.deepseek.com'
MAX_RESPONSE = 262144


class ModelError(ValueError):
    """只含可向用户显示的错误；不携带服务端原文或凭证。"""


def wire_model(name):
    return 'deepseek-flash' if name in {'deepseek-v4-flash', 'deepseek-v4-flash-vision-exp'} else name


def endpoint(base):
    parts = urlsplit(base)
    official = parts.scheme == 'https' and parts.hostname == 'api.deepseek.com' and parts.port in (None, 443)
    local = parts.scheme == 'http' and parts.hostname in {'127.0.0.1', '::1'}
    if not (official or local) or parts.username or parts.password or parts.query or parts.fragment or parts.path.rstrip('/') not in {'', '/v1'}:
        raise ModelError('模型地址只能使用 DeepSeek 官方接口或本机回环地址。')
    return base.rstrip('/') + '/chat/completions', official


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

    def __repr__(self):
        return f'ModelConfig(model={self.model!r}, source={self.source!r})'


def load_config(secrets=None):
    values = secrets or {}
    try:
        store = CredentialStore()
        profile = store.read()
    except CredentialError as exc:
        raise ModelError(str(exc)) from None
    _, desktop_model = desktop_settings()
    base = os.environ.get('BANK_MODEL_BASE_URL') or values.get('BANK_MODEL_BASE_URL') or OFFICIAL
    _, official = endpoint(base)
    key = os.environ.get('BANK_MODEL_API_KEY') or values.get('BANK_MODEL_API_KEY', '') or (os.environ.get('DEEPSEEK_API_KEY', '') if official else '')
    try:
        saved_key = store.key() if official and not key else ''
    except CredentialError as exc:
        raise ModelError(str(exc)) from None
    key = key or saved_key
    model = os.environ.get('BANK_MODEL_NAME') or values.get('BANK_MODEL_NAME') or ((profile['model'] if profile['mode']=='independent' else desktop_model) if official else 'local-model')
    if not isinstance(model, str) or not model.strip() or len(model) > 100 or any(c.isspace() for c in model):
        raise ModelError('模型名称无效。')
    use_desktop = os.environ.get('BANK_MODEL_USE_DESKTOP_PET') == '1' or values.get('BANK_MODEL_USE_DESKTOP_PET') is True or profile['mode'] == 'desktop'
    source = 'configured' if key else ('desktop' if official and use_desktop else ('missing' if official else 'local'))
    return ModelConfig(wire_model(model), base, key, source)


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
        if not isinstance(messages, list) or not 1 <= len(messages) <= 40:
            raise ModelError('对话上下文过长，请开始新的任务。')
        if any(not isinstance(m, dict) or set(m) != {'role', 'content'} or m['role'] not in {'system', 'user', 'assistant'} or not isinstance(m['content'], str) for m in messages):
            raise ModelError('模型消息格式无效。')
        key = self.config.api_key or (desktop_key() if official and self.config.source == 'desktop' else '')
        if official and (not isinstance(key, str) or not 12 <= len(key) <= 512 or any(c.isspace() for c in key)):
            raise ModelError('请先配置模型密钥。')
        body = dict(model=self.config.model, messages=messages, stream=False,
            response_format={'type': 'json_object'}, max_tokens=4096)
        if official:
            body['thinking'] = {'type': 'disabled'}
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
            if not isinstance(text, str) or not text.strip() or (official and wire_model(actual) != wire_model(self.config.model)):
                raise ValueError()
            strict_json_loads(text)
        except (KeyError, IndexError, TypeError, ValueError, UnicodeError):
            raise ModelError('模型未返回完整有效的JSON计划，本次不执行。') from None
        usage = value.get('usage', {})
        if not isinstance(usage, dict):
            usage = {}
        self.last_metadata = {'model': actual, 'credential_source': self.config.source,
            'usage': {k:v for k,v in usage.items() if k in {'prompt_tokens','completion_tokens','total_tokens'} and type(v) is int and v >= 0}}
        return text
