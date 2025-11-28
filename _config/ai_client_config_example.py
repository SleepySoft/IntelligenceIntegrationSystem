# --------------------------------------------------------------------
# Python is config. We don't need a json file and load it, analyze it.
# --------------------------------------------------------------------

from typing import List

from GlobalConfig import *
from AIClientCenter.AIClients import StandardOpenAIClient, \
    SelfRotatingOpenAIClient, OuterTokenRotatingOpenAIClient
from AIClientCenter.AIClientManager import CLIENT_PRIORITY_EXPENSIVE, \
    CLIENT_PRIORITY_FREEBIE, BaseAIClient, CLIENT_PRIORITY_NORMAL
from AIClientCenter.OpenAICompatibleAPI import create_siliconflow_client, create_modelscope_client
from AIClientCenter.AIServiceTokenRotator import SiliconFlowServiceRotator


def build_ai_clients() -> List[BaseAIClient]:
    # -------- The default silicon flow client --------
    # - Use high balance account's token
    # - It is considered available by default.
    # - Once lower value token is available, client manage will not suggest this client.
    # - The Initialize is in environment variant "SILICON_API_KEY"
    # -------------------------------------------------

    sf_api_default = create_siliconflow_client('A valid token')
    sf_client_default = StandardOpenAIClient(
        'SiliconFlow Client Default',
        sf_api_default,
        CLIENT_PRIORITY_EXPENSIVE,
        default_available=True,
        balance_config={ 'hard_threshold': 10 }
    )

    # -------- The token-rotation silicon flow client --------
    # - Use low-value token list.
    # - Init multiple clients, because sf will respond 504 when switching to another key.
    # - Initialize token set to empty.
    # --------------------------------------------------------

    sf_api_a = create_siliconflow_client('invalid')
    sf_client_a = OuterTokenRotatingOpenAIClient(
        'SiliconFlow Client A',
        sf_api_a,
        CLIENT_PRIORITY_NORMAL,
        balance_config={ 'hard_threshold': 0.1 }
    )
    sf_rotator_a = SiliconFlowServiceRotator(
        ai_client=sf_client_a,
        keys_file=os.path.join(CONFIG_PATH, 'sf_keys_a.txt'),
        keys_record_file=os.path.join(DATA_PATH, 'sf_keys_record_a.json'),
        threshold=0.1
    )

    # --------------------------------------------------------

    sf_api_b = create_siliconflow_client('invalid')
    sf_client_b = OuterTokenRotatingOpenAIClient(
        'SiliconFlow Client B',
        sf_api_b,
        CLIENT_PRIORITY_NORMAL,
        balance_config={ 'hard_threshold': 0.1 }
    )
    sf_rotator_b = SiliconFlowServiceRotator(
        ai_client=sf_client_b,
        keys_file=os.path.join(CONFIG_PATH, 'sf_keys_b.txt'),
        keys_record_file=os.path.join(DATA_PATH, 'sf_keys_record_b.json'),
        threshold=0.1
    )

    # -- Start token rotator --

    sf_rotator_a.run_in_thread()
    sf_rotator_b.run_in_thread()

    # -------------- Model scope client --------------
    # - Daily refresh invoking times limit.
    # - Use this client by priority.
    # ------------------------------------------------

    # Modelscope: A total of 2000 free API-Inference calls per day, with a limit of 500 calls per single model
    #             However, only the following three 400B+ models are actually available.

    ms_models = [
        'deepseek-ai/DeepSeek-R1-0528',
        'deepseek-ai/DeepSeek-V3.2-Exp',
        'Qwen/Qwen3-Coder-480B-A35B-Instruct'
    ]

    # --------------------------------------------------------

    ms_api = create_modelscope_client('ms-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx')
    ms_client = SelfRotatingOpenAIClient(
        f'ModelScope Client 1',
        ms_api, CLIENT_PRIORITY_FREEBIE,
        default_available=True
    )
    ms_client.set_rotation_models(ms_models)
    ms_client.set_usage_constraints(max_tokens=495, period_days=1, target_metric='request_count')

    # --------------------------------------------------------

    return [sf_client_default, sf_client_a, sf_client_b, ms_client]


AI_CLIENTS = build_ai_clients()
