import os


PRJ_PATH = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(PRJ_PATH, '_log')
DATA_PATH = os.path.join(PRJ_PATH, '_data')
CONFIG_PATH = os.path.join(PRJ_PATH, '_config')
EXPORT_PATH = os.path.join(PRJ_PATH, '_export')
PRODUCTS_PATH = os.path.join(PRJ_PATH, '_products')


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


DEFAULT_USER_DB_PATH = 'Authentication.db'


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



