"""模型服务商目录：官方目标固定，密钥只用于选定服务商。"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Provider:
    label: str
    base_url: str
    models: tuple
    key_envs: tuple


PROVIDERS = {
    'deepseek': Provider('DeepSeek', 'https://api.deepseek.com',
        ('deepseek-flash', 'deepseek-v4-pro'), ('DEEPSEEK_API_KEY',)),
    'glm': Provider('智谱 GLM', 'https://open.bigmodel.cn/api/paas/v4',
        ('glm-4.7', 'glm-5.2'), ('ZHIPU_API_KEY', 'ZAI_API_KEY')),
    'qwen': Provider('通义千问（阿里云百炼）', 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        ('qwen-plus', 'qwen-flash'), ('DASHSCOPE_API_KEY',)),
    'doubao': Provider('豆包（火山方舟）', 'https://ark.cn-beijing.volces.com/api/v3',
        ('doubao-seed-2-1-pro-260915',), ('ARK_API_KEY',)),
    'kimi': Provider('Kimi（月之暗面）', 'https://api.moonshot.cn/v1',
        ('kimi-k2.6',), ('MOONSHOT_API_KEY',)),
}


def valid_model(name):
    return isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}', name) is not None
