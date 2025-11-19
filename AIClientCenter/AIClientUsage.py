import os
import sys
import time
import logging
import traceback
from typing import Optional, List, Dict

from AIClientCenter.AIClients import OpenAIClient
from AIClientCenter.AIClientManager import CLIENT_PRIORITY_EXPENSIVE, AIClientManager, BaseAIClient, \
    CLIENT_PRIORITY_FREEBIE
from AIClientCenter.OpenAICompatibleAPI import create_siliconflow_client, create_modelscope_client
from AIClientCenter.AIServiceTokenRotator import SiliconFlowServiceRotator


# 1. 定义彩色格式
class ColoredFormatter(logging.Formatter):
    # ANSI 颜色代码
    GREY = "\x1b[38;20m"
    GREEN = "\x1b[32;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    RESET = "\x1b[0m"

    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    FORMATS = {
        logging.DEBUG: GREY + fmt + RESET,
        logging.INFO: GREEN + fmt + RESET,
        logging.WARNING: YELLOW + fmt + RESET,
        logging.ERROR: RED + fmt + RESET,
        logging.CRITICAL: BOLD_RED + fmt + RESET
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


# 2. 配置全局 Root Logger (关键点在这里！)
def setup_colored_logging():
    # 获取 Root Logger (不加名字就是 Root)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)  # 设置全局级别

    # 清空已有的 Handler，防止 PyCharm 重复打印或冲突
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # 创建输出到 stdout 的处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColoredFormatter())

    # 添加到 Root Logger
    root_logger.addHandler(console_handler)


working_path = os.getcwd()
SYSTEM_PROMPT = '你是一个专业的智能人工助手。'


def simple_chat(user_message: str, context: Optional[List[Dict[str, str]]] = None):
    messages = context if context else []
    messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "user", "content": user_message})
    return messages


def main():
    setup_colored_logging()


    sf_api_a = create_siliconflow_client()
    sf_client_a = OpenAIClient('SiliconFlow Client A', sf_api_a, CLIENT_PRIORITY_EXPENSIVE)
    sf_rotator_a = SiliconFlowServiceRotator(
        ai_client=sf_client_a,
        keys_file='siliconflow_keys_a.txt',
        keys_record_file='key_record_a.json',
        threshold=0.1)

    sf_api_b = create_siliconflow_client()
    sf_client_b = OpenAIClient('SiliconFlow Client B', sf_api_b, CLIENT_PRIORITY_EXPENSIVE)
    sf_rotator_b = SiliconFlowServiceRotator(
        ai_client=sf_client_b,
        keys_file='siliconflow_keys_b.txt',
        keys_record_file='key_record_b.json',
        threshold=0.1)

    ms_api = create_modelscope_client()
    ms_api.set_api_token('ms-6800a2c4-472c-4fcd-8e41-1771f847b038')
    ms_client = OpenAIClient('ModelScope Client', ms_api, CLIENT_PRIORITY_FREEBIE, default_available=True)

    client_manager = AIClientManager()
    # client_manager.register_client(sf_client_a)
    # client_manager.register_client(sf_client_b)
    client_manager.register_client(ms_client)
    client_manager.start_monitoring()

    sf_rotator_a.run_in_thread()
    sf_rotator_b.run_in_thread()

    while True:
        client = client_manager.get_available_client()
        if not client:
            print('Client is not available yet.')
        else:
            print(f'Got client {client.name}')
            result = client.chat(messages=simple_chat('请介绍一下你自己。'))
            print(result)
            client_manager.release_client(client)
        time.sleep(2)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(str(e))
        print(traceback.format_exc())
