import os
import time
import traceback
from typing import Optional, List, Dict

from AIClientCenter.AIClients import OpenAIClient
from AIClientCenter.AIClientManager import CLIENT_PRIORITY_EXPENSIVE, AIClientManager, BaseAIClient
from AIClientCenter.OpenAICompatibleAPI import create_siliconflow_client
from AIClientCenter.AIServiceTokenRotator import SiliconFlowServiceRotator


working_path = os.getcwd()
SYSTEM_PROMPT = '你是一个专业的智能人工助手。'


def simple_chat(user_message: str, context: Optional[List[Dict[str, str]]] = None):
    messages = context if context else []
    messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "user", "content": user_message})
    return messages


def main():
    sf_api_a = create_siliconflow_client()
    sf_client_a = OpenAIClient(sf_api_a, CLIENT_PRIORITY_EXPENSIVE)
    sf_rotator_a = SiliconFlowServiceRotator(
        ai_client=sf_api_a,
        keys_file='siliconflow_keys_a.txt',
        keys_record_file='key_record_a.json',
        threshold=0.1)

    sf_api_b = create_siliconflow_client()
    sf_client_b = OpenAIClient(sf_api_b, CLIENT_PRIORITY_EXPENSIVE)
    sf_rotator_b = SiliconFlowServiceRotator(
        ai_client=sf_api_b,
        keys_file='siliconflow_keys_b.txt',
        keys_record_file='key_record_b.json',
        threshold=0.1)

    client_manager = AIClientManager()
    client_manager.register_client(sf_client_a)
    client_manager.register_client(sf_client_b)
    client_manager.start_monitoring()

    sf_rotator_a.run_in_thread()
    sf_rotator_b.run_in_thread()

    while True:
        client = client_manager.get_available_client()
        if not client:
            print('Client is not available yet.')
        else:
            client.chat(messages=simple_chat('请介绍一下你自己。'))
            client_manager.release_client(client)
        time.sleep(2)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(str(e))
        print(traceback.format_exc())
