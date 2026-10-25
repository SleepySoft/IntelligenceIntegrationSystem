import os


SOURCE_PATH = os.path.dirname(os.path.abspath(__file__))
# PRJ_PATH 历史上还用于寻找 posts、扩展资源等源码内容，因此保持为源码根；配置和运行
# 数据通过独立环境变量外置，避免把源码根与可写数据根混为一谈。
PRJ_PATH = SOURCE_PATH


def _runtime_path(env_name, default_name):
    value = os.environ.get(env_name, '').strip()
    if value:
        return os.path.abspath(os.path.expanduser(value))
    return os.path.join(SOURCE_PATH, default_name)


LOG_PATH = _runtime_path('IIS_LOG_DIR', '_log')
DATA_PATH = _runtime_path('IIS_DATA_DIR', '_data')
CONFIG_PATH = _runtime_path('IIS_CONFIG_DIR', '_config')
EXPORT_PATH = _runtime_path('IIS_EXPORT_DIR', '_export')
PRODUCTS_PATH = _runtime_path('IIS_PRODUCTS_DIR', '_products')
MODEL_PATH = _runtime_path('IIS_MODEL_DIR', '_models')


DEFAULT_PROXY = {
    "http": "socks5://127.0.0.1:10808",
    "https": "socks5://127.0.0.1:10808"
}


# Not that if you're using proxy or vpn. The situation will be opposite.
DEFAULT_INTERNAL_TIMEOUT_MS = 20000         # Internal network timeout
DEFAULT_NATIONAL_TIMEOUT_MS = 35000         # National network timeout


DEFAULT_RPC_API_TOKEN = 'SleepySoft'
DEFAULT_COLLECTOR_TOKEN = 'SleepySoft'
DEFAULT_PROCESSOR_TOKEN = 'SleepySoft'


CONFIG_FILE = 'iis.yaml'
ALTER_CONFIG_FILE = 'iis_example.yaml'
DEFAULT_CONFIG_FILE = os.path.join(CONFIG_PATH, CONFIG_FILE)
# 示例属于源码资源而不是部署配置。外置 IIS_CONFIG_DIR 时仍可供开发模式回退；生产入口
# 一旦显式设置 IIS_CONFIG，则禁止回退到示例。
DEFAULT_ALTER_CONFIG_FILE = os.path.join(SOURCE_PATH, '_config', ALTER_CONFIG_FILE)


def resolve_iis_config_files():
    """返回正式配置与可选回退文件；显式 IIS_CONFIG 永不回退示例。"""

    explicit = os.environ.get('IIS_CONFIG', '').strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit)), None
    return DEFAULT_CONFIG_FILE, DEFAULT_ALTER_CONFIG_FILE


USER_DB_FILE = 'Authentication.db'
DEFAULT_USER_DB_PATH = os.path.join(CONFIG_PATH, USER_DB_FILE)


USING_VPN = True
USING_PROXY = False


if not USING_PROXY:
    APPLIED_PROXY = {}
else:
    APPLIED_PROXY = DEFAULT_PROXY


if USING_VPN or USING_PROXY:
    APPLIED_INTERNAL_TIMEOUT_MS = DEFAULT_NATIONAL_TIMEOUT_MS
    APPLIED_NATIONAL_TIMEOUT_MS = DEFAULT_INTERNAL_TIMEOUT_MS
else:
    APPLIED_INTERNAL_TIMEOUT_MS = DEFAULT_INTERNAL_TIMEOUT_MS
    APPLIED_NATIONAL_TIMEOUT_MS = DEFAULT_NATIONAL_TIMEOUT_MS


DEFAULT_IHUB_PORT = 5000
DEFAULT_MONGO_DB_URL = "mongodb://localhost:27017/"
DEFAULT_AI_PROCESSOR_URL = "http://localhost:5001/process"

MODEL_SILICON_FLOW_QWEN = 'Qwen/Qwen3-235B-A22B'
MODEL_OLLAMA_DEEP_SEEK_R1 = 'deepseek-r1:14b'
MODEL_OLLAMA_QWEN3_14B = 'qwen3:14b'
MODEL_SELECT = MODEL_OLLAMA_QWEN3_14B

OPEN_AI_API_BASE_URL_LOCAL_OLLAMA = "http://localhost:11434"
OPEN_AI_API_BASE_URL_SILICON_FLOW = "https://api.siliconflow.cn"
OPEN_AI_API_BASE_URL_SELECT = OPEN_AI_API_BASE_URL_LOCAL_OLLAMA
