"""当前Windows用户的本机模型配置；明文密钥只存在于内存。"""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from uuid import uuid4

PROFILE = Path(__file__).resolve().parents[1] / '.cache' / 'bank-model' / 'profile.json'


class CredentialError(ValueError):
    pass


def _dpapi(raw, *, decrypt):
    if os.name != 'nt':
        raise CredentialError('本机加密保存仅支持Windows；其他系统请使用服务器环境变量。')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.restype = wintypes.BOOL
    ok = fn(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    try:
        if not ok:
            raise CredentialError('当前用户无法使用Windows加密存储，请改用环境变量配置。')
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.memset(source, 0, len(raw))
        if outgoing.data:
            ctypes.memset(outgoing.data, 0, outgoing.size)
            kernel.LocalFree(outgoing.data)


class CredentialStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else PROFILE

    def _safe(self):
        if self.path.is_symlink() or self.path.resolve() != self.path.absolute():
            raise CredentialError('模型配置路径不能使用链接。')
        if self.path.exists() and self.path.stat().st_nlink > 1:
            raise CredentialError('模型配置不能使用硬链接。')

    def read(self):
        self._safe()
        if not self.path.exists():
            return {'version':1, 'mode':'missing', 'model':'deepseek-flash'}
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or value.get('version') != 1 or value.get('mode') not in {'missing','desktop','independent'}:
                raise ValueError()
            if value.get('model') not in {'deepseek-flash','deepseek-v4-pro'}:
                raise ValueError()
            if 'encrypted_key' in value and not isinstance(value['encrypted_key'], str):
                raise ValueError()
            return value
        except (OSError, ValueError):
            raise CredentialError('模型本机配置无法读取，请重新保存设置。') from None

    def save(self, *, mode, model='deepseek-flash', key=None, clear=False):
        if mode not in {'missing','desktop','independent'} or model not in {'deepseek-flash','deepseek-v4-pro'}:
            raise CredentialError('模型设置无效。')
        value = self.read()
        if key is not None:
            if not isinstance(key, str) or not 12 <= len(key.strip()) <= 512 or any(c.isspace() for c in key.strip()):
                raise CredentialError('密钥格式无效，请重新输入。')
            value['encrypted_key'] = base64.b64encode(_dpapi(key.strip().encode(), decrypt=False)).decode('ascii')
        if clear:
            value.pop('encrypted_key', None)
        if mode == 'independent' and not value.get('encrypted_key'):
            raise CredentialError('请先输入银行项目独立密钥。')
        value.update(mode=mode, model=model)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._safe()
        temp = self.path.with_name('profile-' + uuid4().hex + '.tmp')
        try:
            with temp.open('x', encoding='utf-8') as handle:
                json.dump(value, handle, ensure_ascii=False)
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)

    def key(self):
        value = self.read()
        if value['mode'] != 'independent':
            return ''
        try:
            return _dpapi(base64.b64decode(value['encrypted_key'], validate=True), decrypt=True).decode('utf-8')
        except (ValueError, KeyError, UnicodeError):
            raise CredentialError('无法解密银行项目密钥，请重新保存。') from None
