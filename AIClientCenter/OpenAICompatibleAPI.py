import os
import json
import time
import asyncio
import aiohttp
import backoff
import logging
import requests
import threading
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from typing import Optional, Dict, Any, Union, List

try:
    import aiohttp
except:
    aiohttp = None

logger = logging.getLogger(__name__)

"""
Siliconflow Reply example:
{
  "id": "0196f08d74220b683a08ca3630683a51",         # 唯一标识符，用于追踪API调用记录
  "object": "chat.completion",                      # 标识响应类型，chat.completion表示这是聊天补全类型的响应
  "created": 1747792524,                            # Unix时间戳，表示API请求处理完成的时间（示例值1747792524对应北京时间2025-05-21 15:55:24）
  "model": "Qwen/Qwen3-235B-A22B",                  # 实际使用的模型标识，示例中Qwen/Qwen3-235B-A22B表明调用了第三方适配的千问模型
  "choices": [                                      # 包含生成结果的容器，常规场景下仅有一个元素
    {
      "index": 0,                                   # 候选结果的序号（多候选时有效）
      "message": {                                  # 生成的对话消息对象
        "role": "assistant",                        # 消息来源标识（assistant表示AI生成）
        "content": ""                               # 实际生成的文本内容 <-- **需要关注该内容**
		},
      "finish_reason": "stop"                       # 生成终止原因，"stop"表示自然结束
    }
  ],
  "usage": {                                        # 资源消耗统计
    "prompt_tokens": 22,                            # 输入消耗的token数
    "completion_tokens": 254,                       # 输出消耗的token数
    "total_tokens": 276,                            # 总token数
    "completion_tokens_details": {                  # 扩展字段
      "reasoning_tokens": 187                       # 推理过程消耗的token数
    }
  },
  "system_fingerprint": ""                          # 系统指纹标识，用于追踪模型版本信息（示例为空说明未启用该功能）
}
"""

# --- Helper constants and functions for retry logic ---

# Status codes that are safe to retry on
RETRYABLE_STATUS_CODES = {
    429,  # Too Many Requests
    500,  # Internal Server Error
    502,  # Bad Gateway
    503,  # Service Unavailable
    504  # Gateway Timeout
}

# Exceptions that indicate a transient (retryable) async error
RETRYABLE_ASYNC_EXCEPTIONS = (
    asyncio.TimeoutError,
    aiohttp.ClientConnectionError
)


LLM_DEFAULT_TIMEOUT_S = 10 * 60


def is_retryable_async_error(e: Exception) -> bool:
    """
    Check if an exception from aiohttp is transient and can be retried.

    Args:
        e (Exception): The exception that was raised.

    Returns:
        bool: True if the error is retryable, False otherwise.
    """
    # Check for network/timeout errors first
    if isinstance(e, RETRYABLE_ASYNC_EXCEPTIONS):
        return True

    # Check for HTTP response errors with a retryable status code
    if isinstance(e, aiohttp.ClientResponseError):
        return e.status in RETRYABLE_STATUS_CODES

    # All other exceptions are not retryable
    return False


# --- End of helper definitions ---


class OpenAICompatibleAPI:
    """
    A client class to interact with OpenAI-like API services.

    This class provides a flexible way to communicate with APIs that are compatible with OpenAI's interface.
    It supports both synchronous and asynchronous requests and provides token authentication handling.

    It also implements robust exponential backoff retries for both sync and async requests.
    - Sync methods use requests.Session with urllib3.Retry.
    - Async methods use the @backoff decorator.

    Attributes:
        api_base_url (str): The base URL of the API service.
        _api_token (str): Authentication token for the API.
        default_model (str): Default model to use when making requests.
        proxies (dict): Proxies to use for requests.
        sync_session (requests.Session): A session for synchronous requests with retry logic.
    """

    def __init__(self, api_base_url: str, token: Optional[str] = None,
                 default_model: str = "gpt-3.5-turbo", proxies: dict = None):
        """
        Initialize the OpenAI-compatible API client.

        Args:
            api_base_url (str): The base URL of the API service.
            token (Optional[str]): Authentication token for the API. If not provided, it will be fetched from environment variables.
            default_model (str): Default model to use when making requests.
            proxies (dict): Proxies to use for requests.

        Raises:
            ValueError: If token is not provided and not found in environment variables.
        """
        self.api_base_url = api_base_url.strip()

        # Try to get token from environment variables if not provided
        self._api_token = token or os.getenv("OPENAI_API_KEY")

        self.default_model = default_model
        self.proxies = proxies or {}

        # This lock protects self.api_token, which is read by both sync and async methods
        self.lock = threading.Lock()

        # Create a persistent session for synchronous requests
        # This session will handle connection pooling and retries automatically
        self.sync_session = self._create_sync_session()

    def _create_sync_session(self) -> requests.Session:
        """
        Create a requests.Session configured with exponential backoff.

        Returns:
            requests.Session: A session object with retry logic mounted.
        """
        session = requests.Session()

        # Define the retry strategy
        retry_strategy = Retry(
            total=5,  # Total number of retries
            backoff_factor=25,  # Base for exponential backoff (1s, 2s, 4s...)
            status_forcelist=list(RETRYABLE_STATUS_CODES),  # HTTP codes to retry on
            allowed_methods=["GET", "POST"]  # Only retry these idempotent/safe methods
        )

        # Create an adapter and mount the retry strategy
        adapter = HTTPAdapter(max_retries=retry_strategy)

        session.mount("https://", adapter)
        session.mount("http://", adapter)

        # Set default headers and proxies for the session
        session.headers.update({
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_token}"
        })
        session.proxies = self.proxies

        return session

    def _construct_url(self, endpoint: str) -> str:
        """Construct the full URL for a given API endpoint."""
        base = self.api_base_url.rstrip('/')
        return f"{base}/{endpoint}"

    def _prepare_request_data(self,
                              model: Optional[str] = None,
                              messages: Optional[List[Dict[str, str]]] = None,
                              **kwargs) -> Dict[str, Any]:
        """
        Prepare data for the API request.

        Args:
            model (Optional[str]): Model to use for the request. Defaults to the client's default_model.
            messages (Optional[List[Dict[str, str]]]): List of message objects for chat completion.
            **kwargs: Additional parameters to include in the request.

        Returns:
            Dict[str, Any]: Prepared request data.
        """
        request_data = {
            "model": model or self.default_model,
            **kwargs
        }

        # Add messages if provided for chat completion
        if messages:
            request_data["messages"] = messages

        return request_data

    def get_api_token(self) -> str:
        with self.lock:
            return self._api_token

    def set_api_token(self, token: str):
        """Safely update the API token for both sync and async methods."""
        with self.lock:
            old_token = self._api_token
            self._api_token = token
            # Update the token in the persistent synchronous session
            self.sync_session.headers["Authorization"] = f"Bearer {self._api_token}"
            if old_token:
                logger.info(f'Change API key from {old_token[:16]} to {token[:16]}.')
            else:
                logger.info(f'Set API key: {token[:16]}.')

    def get_header(self) -> dict:
        """
        Get headers for async requests.
        Note: Sync requests use self.sync_session which manages its own headers.
        """
        with self.lock:
            return {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_token}"
            }

    def get_model_list(self) -> Union[Dict[str, Any], requests.Response]:
        """
        Get the list of available models from the API.
        Retries are handled automatically by self.sync_session.

        Returns:
            Union[Dict[str, Any], requests.Response]: The API response, either as a parsed dictionary or the raw response object.
        """
        if not self._api_token:
            return {'error': 'invalid api token'}

        url = self._construct_url("models")

        try:
            # Use the pre-configured session. Headers, proxies, and retries are automatic.
            response = self.sync_session.get(url, timeout=LLM_DEFAULT_TIMEOUT_S)  # Add a reasonable timeout

            # If retries failed, status_code will be the last error (e.g., 503)
            return response.json() if response.status_code == 200 else response

        except requests.exceptions.RequestException as e:
            # This catches errors if all retries fail (e.g., ConnectionError)
            logger.error(f"Max retries exceeded for get_model_list: {e}")
            return {'error': f'Max retries reached. Last error: {str(e)}'}


    def create_chat_completion_sync(self,
                                    messages: List[Dict[str, str]],
                                    model: Optional[str] = None,
                                    temperature: float = 0.7,
                                    max_tokens: int = 4096) -> Union[Dict[str, Any], requests.Response]:
        """
        Create a chat completion synchronously.
        Retries are handled automatically by self.sync_session.

        Args:
            messages (List[Dict[str, str]]): List of message objects for the conversation.
            model (Optional[str]): Model to use for the completion. Defaults to the client's default_model.
            temperature (float): Controls randomness. Lower values give more deterministic results.
            max_tokens (int): Maximum number of tokens to generate in the response.

        Returns:
            Union[Dict[str, Any], requests.Response]: The API response, either as a parsed dictionary or the raw response object.

        Note:
            The messages should be in the format of [{"role": "system", "content": "System message"},
                                                   {"role": "user", "content": "User message"}].
        """
        if not self._api_token:
            return {'error': 'invalid api token'}

        url = self._construct_url("chat/completions")
        data = self._prepare_request_data(
            model=model or self.default_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )

        try:
            # Use the pre-configured session.
            response = self.sync_session.post(url, json=data, timeout=LLM_DEFAULT_TIMEOUT_S)  # Longer timeout for generation

            # Return parsed JSON if successful, otherwise return the raw response
            return response.json() if response.status_code == 200 else response

        except requests.exceptions.RequestException as e:
            # This catches errors if all retries fail
            logger.error(f"Max retries exceeded for create_chat_completion_sync: {e}")
            return {'error': f'Max retries reached. Last error: {str(e)}'}

    # Use the 'backoff' decorator for exponential backoff on async methods
    @backoff.on_exception(
        backoff.expo,  # Use exponential strategy
        RETRYABLE_ASYNC_EXCEPTIONS,  # Retry on these exception classes
        max_tries=4,  # Try a total of 4 times (1 initial + 3 retries)
        giveup=lambda e: not is_retryable_async_error(e)  # Give up if error is not retryable
    )
    async def create_chat_completion_async(self,
                                           messages: List[Dict[str, str]],
                                           model: Optional[str] = None,
                                           temperature: float = 0.7,
                                           max_tokens: int = 4096) -> Dict[str, Any]:
        """
        Create a chat completion asynchronously.
        Retries are handled by the @backoff decorator.

        Args:
            messages (List[Dict[str, str]]): List of message objects for the conversation.
            model (Optional[str]): Model to use for the completion. Defaults to the client's default_model.
            temperature (float): Controls randomness. Lower values give more deterministic results.
            max_tokens (int): Maximum number of tokens to generate in the response.

        Returns:
            Dict[str, Any]: The API response as a parsed dictionary.

        Raises:
            aiohttp.ClientResponseError: If all retries fail with an HTTP error.
            aiohttp.ClientConnectionError: If all retries fail with a connection error.

        Note:
            Requires asyncio and aiohttp to be installed and used within an async context.
            The messages should be in the format of [{"role": "system", "content": "System message"},
                                                   {"role": "user", "content": "User message"}].
        """
        if not self._api_token:
            return {'error': 'invalid api token'}
        if not aiohttp:
            return {'error': 'aiohttp not installed'}

        url = self._construct_url("chat/completions")
        data = self._prepare_request_data(
            model=model or self.default_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )

        # Use async POST request to send the completion request
        async with aiohttp.ClientSession() as session:
            async with session.post(
                    url,
                    headers=self.get_header(),  # Get fresh headers (thread-safe)
                    json=data,
                    proxy=self._get_url_proxy(url),
                    timeout=LLM_DEFAULT_TIMEOUT_S  # Set a timeout
            ) as response:
                # This is key: it raises an exception for 4xx/5xx status codes,
                # which allows the @backoff decorator to catch them.
                response.raise_for_status()
                return await response.json()

    def create_completion_sync(self,
                               prompt: str,
                               model: Optional[str] = None,
                               temperature: float = 0.7,
                               max_tokens: int = 4096) -> Union[Dict[str, Any], requests.Response]:
        """
        Create a text completion synchronously.
        Retries are handled automatically by self.sync_session.

        Args:
            prompt (str): The text prompt to generate completion for.
            model (Optional[str]): Model to use for the completion. Defaults to the client's default_model.
            temperature (float): Controls randomness. Lower values give more deterministic results.
            max_tokens (int): Maximum number of tokens to generate in the response.

        Returns:
            Union[Dict[str, Any], requests.Response]: The API response, either as a parsed dictionary or the raw response object.
        """
        if not self._api_token:
            return {'error': 'invalid api token'}

        url = self._construct_url("completions")
        data = self._prepare_request_data(
            model=model or self.default_model,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens
        )

        try:
            # Use the pre-configured session.
            response = self.sync_session.post(url, json=data, timeout=LLM_DEFAULT_TIMEOUT_S)

            # Return parsed JSON if successful, otherwise return the raw response
            return response.json() if response.status_code == 200 else response

        except requests.exceptions.RequestException as e:
            # This catches errors if all retries fail
            logger.error(f"Max retries exceeded for create_completion_sync: {e}")
            return {'error': f'Max retries reached. Last error: {str(e)}'}

    @backoff.on_exception(
        backoff.expo,
        RETRYABLE_ASYNC_EXCEPTIONS,
        max_tries=4,
        giveup=lambda e: not is_retryable_async_error(e)
    )
    async def create_completion_async(self,
                                      prompt: str,
                                      model: Optional[str] = None,
                                      temperature: float = 0.7,
                                      max_tokens: int = 4096) -> Dict[str, Any]:
        """
        Create a text completion asynchronously.
        Retries are handled by the @backoff decorator.

        Args:
            prompt (str): The text prompt to generate completion for.
            model (Optional[str]): Model to use for the completion. Defaults to the client's default_model.
            temperature (float): Controls randomness. Lower values give more deterministic results.
            max_tokens (int): Maximum number of tokens to generate in the response.

        Returns:
            Dict[str, Any]: The API response as a parsed dictionary.

        Raises:
            aiohttp.ClientResponseError: If all retries fail with an HTTP error.
            aiohttp.ClientConnectionError: If all retries fail with a connection error.

        Note:
            Requires asyncio and aiohttp to be installed and used within an async context.
        """
        if not self._api_token:
            return {'error': 'invalid api token'}
        if not aiohttp:
            return {'error': 'aiohttp not installed'}

        url = self._construct_url("completions")
        data = self._prepare_request_data(
            model=model or self.default_model,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens
        )

        # Use async POST request to send the completion request
        async with aiohttp.ClientSession() as session:
            async with session.post(
                    url,
                    headers=self.get_header(),
                    json=data,
                    proxy=self._get_url_proxy(url),
                    timeout=LLM_DEFAULT_TIMEOUT_S
            ) as response:
                # Trigger the @backoff decorator on 4xx/5xx errors
                response.raise_for_status()
                return await response.json()


    def _get_url_proxy(self, url: str) -> Optional[str]:
        """Helper to get the correct proxy (http/https) for aiohttp."""
        if not self.proxies:
            return None
        return self.proxies.get("https") if url.startswith("https") else self.proxies.get("http")


# ----------------------------------------------------------------------------------------------------------------------

def create_ollama_client(model: Optional[str] = None):
    client = OpenAICompatibleAPI(
        api_base_url='http://localhost:11434/v1',
        token='x',
        default_model=model or 'qwen3:14b'
    )
    return client


def create_siliconflow_client(token: Optional[str] = None, model: Optional[str] = None):
    client = OpenAICompatibleAPI(
        api_base_url='https://api.siliconflow.cn/v1',
        token=token or os.getenv("SILICON_API_KEY"),
        default_model=model or 'Qwen/Qwen3-235B-A22B'
    )
    return client


def create_modelscope_client(token: Optional[str] = None, model: Optional[str] = None):
    client = OpenAICompatibleAPI(
        api_base_url='https://api-inference.modelscope.cn/v1',
        token=token or os.getenv("MODELSCOPE_API_KEY"),
        default_model=model or 'deepseek-ai/DeepSeek-V3.2-Exp'
    )
    return client


def create_gemini_client(token: Optional[str] = None, model: Optional[str] = None):
    client = OpenAICompatibleAPI(
        api_base_url='https://generativelanguage.googleapis.com/v1beta/openai',
        token=token or os.getenv("GEMINI_API_KEY"),
        default_model=model or 'models/gemini-pro-latest',
        proxies={
            "http": "http://127.0.0.1:10809",
            "https": "http://127.0.0.1:10809"
        }
    )
    return client


def main():
    try:
        from MyPythonUtility.DictTools import DictPrinter
    except Exception as e:
        print(str(e))
        DictPrinter = None
    finally:
        pass

    # Initialize the client - token can be passed directly or will be fetched from environment
    client = create_gemini_client()

    model_list = client.get_model_list()
    print(f'Model list of {client.api_base_url}')

    if isinstance(model_list, dict) and DictPrinter:
        print(DictPrinter.pretty_print(model_list))
    else:
        print(model_list)

    # Example synchronous chat completion
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "你是谁?"}
    ]

    response = client.create_chat_completion_sync(messages=messages)
    print("Synchronous chat completion response:")
    print(json.dumps(response, indent=2, ensure_ascii=False))

    # # Example synchronous text completion
    # prompt = "Once upon a time in a land far away,"
    # response = client.create_completion_sync(prompt=prompt)
    # print("\nSynchronous text completion response:")
    # print(json.dumps(response, indent=2))

    # Example asynchronous usage requires asyncio
    import asyncio

    async def async_demo():
        # Example async chat completion
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the meaning of life?"}
        ]

        response = await client.create_chat_completion_async(messages=messages)
        print("\nAsynchronous chat completion response:")
        print(json.dumps(response, indent=2))

        # Example async text completion
        prompt = "The capital of France is"
        response = await client.create_completion_async(prompt=prompt)
        print("\nAsynchronous text completion response:")
        print(json.dumps(response, indent=2))

    # Run the async demo
    asyncio.run(async_demo())


# Example usage
if __name__ == "__main__":
    main()
