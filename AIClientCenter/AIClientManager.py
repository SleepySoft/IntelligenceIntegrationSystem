import time
import logging
import datetime
import threading
from enum import Enum
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Union

import requests

logger = logging.getLogger(__name__)


class ClientStatus(Enum):
    """Status of AI client"""
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class BaseAIClient(ABC):
    """
    Abstract base class for AI clients with common management interface.
    Extends the existing OpenAICompatibleAPI functionality.
    """

    def __init__(self, api_token: str, priority: int = 1):
        """
        Initialize AI client with token and priority.

        Args:
            api_token: API token for authentication
            priority: Client priority (lower number = higher priority)
        """
        self.api_token = api_token
        self.priority = priority
        self.status = ClientStatus.UNKNOWN
        self.last_checked = None
        self.error_count = 0
        self.max_errors = 3
        self._lock = threading.RLock()
        self._in_use = False
        self._last_used = None
        self._usage_stats = { "last_update": None }

        # Testing configuration
        self.test_interval = 300  # 5 minutes default
        self.last_test_time = 0
        self.test_prompt = "Hello, are you working? Please respond with 'OK'."
        self.expected_response = "OK"

    def acquire(self) -> bool:
        """
        Attempt to acquire the client for use.

        Returns:
            bool: True if acquired successfully
        """
        with self._lock:
            if self._in_use or self.status == ClientStatus.UNAVAILABLE:
                return False

            self._in_use = True
            self._last_used = time.time()
            return True

    def release(self):
        """Release the client after use."""
        with self._lock:
            self._in_use = False

    def is_busy(self) -> bool:
        """Check if client is currently in use."""
        return self._in_use

    def get_status(self) -> ClientStatus:
        """Get current client status."""
        return self.status

    def record_usage(self, usages: dict):
        """Record usage statistics."""
        with self._lock:
            self._usage_stats.update(usages)
            self._usage_stats["last_update"] = datetime.datetime.now()

    def get_usage_stats(self) -> Dict[str, Any]:
        """Get usage statistics."""
        with self._lock:
            return self._usage_stats.copy()

    def chat(self,
             messages: List[Dict[str, str]],
             model: Optional[str] = None,
             temperature: float = 0.7,
             max_tokens: int = 4096) -> Dict[str, Any]:
        try:
            response = self.chat_completion_sync(messages, model, temperature, max_tokens)

            # 处理HTTP错误响应
            if hasattr(response, 'status_code') and response.status_code != 200:
                return self._handle_http_error(response)

            # 处理API响应错误
            if isinstance(response, dict) and 'error' in response:
                return self._handle_api_error(response)

            # 处理成功的LLM响应，检查业务逻辑错误
            if isinstance(response, dict) and 'choices' in response:
                return self._handle_llm_response(response, messages)

            # 未知响应格式
            logger.error(f"Unknown response format: {type(response)}")
            return {'error': 'unknown_response_format', 'message': 'Received unexpected response format'}

        except Exception as e:
            return self._handle_exception(e)

    def _handle_http_error(self, response) -> Dict[str, Any]:
        """处理HTTP错误状态码"""
        error_info = {
            'status_code': response.status_code,
            'reason': getattr(response, 'reason', 'Unknown'),
            'headers': dict(response.headers) if hasattr(response, 'headers') else {}
        }

        # 根据状态码分类错误类型
        if response.status_code in [400, 422]:
            # 错误请求 - 通常是参数错误，可能是可恢复的
            error_type = 'recoverable'
            logger.warning(f"Bad request error (recoverable): {response.status_code}")
            self.error_count += 1

        elif response.status_code == 401:
            # 认证失败 - 通常是不可恢复的致命错误
            error_type = 'fatal'
            logger.error("Authentication failed - invalid API token")
            self._update_status(ClientStatus.UNAVAILABLE)
            self.error_count = self.max_errors  # 立即达到错误阈值

        elif response.status_code == 403:
            # 权限不足 - 可能是不可恢复的
            error_type = 'fatal'
            logger.error("Permission denied - check API permissions")
            self.error_count += 2  # 权限错误权重更高

        elif response.status_code == 429:
            # 速率限制 - 可恢复错误，需要延迟重试
            error_type = 'recoverable'
            retry_after = response.headers.get('Retry-After', 60)
            logger.warning(f"Rate limit exceeded, retry after {retry_after}s")
            # 可以在这里实现延迟重试逻辑

        elif response.status_code >= 500:
            # 服务器错误 - 通常是临时的，可恢复
            error_type = 'recoverable'
            logger.warning(f"Server error {response.status_code}, may be temporary")
            self.error_count += 1

        else:
            # 其他HTTP错误
            error_type = 'recoverable'
            logger.warning(f"HTTP error {response.status_code}")
            self.error_count += 1

        # 检查错误计数是否超过阈值
        if self.error_count >= self.max_errors:
            self._update_status(ClientStatus.UNAVAILABLE)

        return {
            'error': 'http_error',
            'error_type': error_type,
            'status_code': response.status_code,
            'message': f"HTTP error {response.status_code}: {getattr(response, 'reason', 'Unknown')}"
        }

    def _handle_api_error(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """处理API返回的业务错误"""
        error_data = response.get('error', {})
        error_message = error_data.get('message', 'Unknown API error') if isinstance(error_data, dict) else str(
            error_data)
        error_type = error_data.get('type', 'unknown') if isinstance(error_data, dict) else 'unknown'

        # 根据错误类型分类
        fatal_errors = {'invalid_request_error', 'insufficient_quota', 'billing_hard_limit_reached'}
        recoverable_errors = {'rate_limit_exceeded', 'server_error', 'timeout'}

        if error_type in fatal_errors:
            error_category = 'fatal'
            logger.error(f"Fatal API error: {error_message} (type: {error_type})")
            self._update_status(ClientStatus.UNAVAILABLE)
            self.error_count = self.max_errors
        elif error_type in recoverable_errors:
            error_category = 'recoverable'
            logger.warning(f"Recoverable API error: {error_message}")
            self.error_count += 1
        else:
            error_category = 'recoverable'
            logger.warning(f"Unknown API error type: {error_type}, message: {error_message}")
            self.error_count += 1

        # 检查错误计数阈值
        if self.error_count >= self.max_errors:
            self._update_status(ClientStatus.UNAVAILABLE)

        return {
            'error': 'api_error',
            'error_type': error_category,
            'api_error_type': error_type,
            'message': error_message
        }

    def _handle_llm_response(self, response: Dict[str, Any], original_messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """处理成功的LLM响应，检查业务逻辑错误并统计使用情况"""
        try:
            choices = response.get('choices', [])
            if not choices:
                return {
                    'error': 'empty_response',
                    'error_type': 'recoverable',
                    'message': 'API returned empty choices array'
                }

            first_choice = choices[0]
            finish_reason = first_choice.get('finish_reason')

            # 检查完成原因是否表示错误
            if finish_reason in ['length', 'content_filter']:
                error_type = 'recoverable'
                logger.warning(f"LLM response truncated due to: {finish_reason}")
                self.error_count += 0.5  # 部分错误，权重较低

                return {
                    'error': 'llm_generation_issue',
                    'error_type': error_type,
                    'finish_reason': finish_reason,
                    'message': f"Response generation issue: {finish_reason}",
                    'choices': choices  # 仍然返回部分结果
                }

            # 统计token使用量
            usage_data = response.get('usage', {})
            if usage_data:
                self._record_token_usage(usage_data, original_messages)

            # 重置错误计数（成功请求）
            self.error_count = 0
            self._update_status(ClientStatus.AVAILABLE)

            return response

        except Exception as e:
            logger.error(f"Error processing LLM response: {e}")
            return {
                'error': 'response_processing_error',
                'error_type': 'recoverable',
                'message': f'Failed to process LLM response: {str(e)}'
            }

    def _handle_exception(self, exception: Exception) -> Dict[str, Any]:
        """处理异常情况"""
        error_message = str(exception)

        # 根据异常类型分类
        if isinstance(exception, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            # 网络相关异常 - 通常是可恢复的
            error_type = 'recoverable'
            logger.warning(f"Network error (recoverable): {error_message}")
            self.error_count += 1

        elif isinstance(exception, requests.exceptions.RequestException):
            # 其他请求异常
            error_type = 'recoverable'
            logger.warning(f"Request exception: {error_message}")
            self.error_count += 1

        elif isinstance(exception, (ValueError, TypeError)):
            # 参数错误 - 可能是不可恢复的编程错误
            error_type = 'fatal'
            logger.error(f"Parameter error (fatal): {error_message}")
            self.error_count += 2

        else:
            # 其他未知异常
            error_type = 'recoverable'
            logger.error(f"Unexpected error: {error_message}")
            self.error_count += 1

        # 检查错误计数阈值
        if self.error_count >= self.max_errors:
            self._update_status(ClientStatus.UNAVAILABLE)

        return {
            'error': 'exception',
            'error_type': error_type,
            'exception_type': type(exception).__name__,
            'message': error_message
        }

    def _record_token_usage(self, usage_data: Dict[str, Any], original_messages: List[Dict[str, str]]):
        """记录token使用统计"""
        with self._lock:
            # 更新使用统计
            current_stats = {
                'prompt_tokens': usage_data.get('prompt_tokens', 0),
                'completion_tokens': usage_data.get('completion_tokens', 0),
                'total_tokens': usage_data.get('total_tokens', 0),
                'message_count': len(original_messages),
                'last_update': datetime.datetime.now()
            }

            # 合并历史统计
            self._usage_stats.update(current_stats)

            # 计算累计值
            if 'total_prompt_tokens' not in self._usage_stats:
                self._usage_stats['total_prompt_tokens'] = 0
            if 'total_completion_tokens' not in self._usage_stats:
                self._usage_stats['total_completion_tokens'] = 0

            self._usage_stats['total_prompt_tokens'] += current_stats['prompt_tokens']
            self._usage_stats['total_completion_tokens'] += current_stats['completion_tokens']

    # ------------------------------------------------------------------------------------------------------------------

    @abstractmethod
    def get_usage_metrics(self) -> Dict[str, float]:
        """
        Get usage metrics and return the most critical remaining percentage.

        Returns:
            Dict with usage metrics including 'remaining_percentage' (0-100)
        """
        pass

    @abstractmethod
    def get_model_list(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def chat_completion_sync(self,
                             messages: List[Dict[str, str]],
                             model: Optional[str] = None,
                             temperature: float = 0.7,
                             max_tokens: int = 4096) -> Union[Dict[str, Any], requests.Response]:
        pass

    def test_and_update_status(self):
        """
        Test client connectivity and update status.
        Called periodically by the management framework.

        Returns:
            bool: True if test was successful
        """
        current_time = time.time()
        if current_time - self.last_test_time < self.test_interval:
            return

        if not self.acquire():
            return

        try:
            result = self.create_chat_completion_sync(
                messages=[{"role": "user", "content": self.test_prompt}],
                max_tokens=100
            )

            if (isinstance(result, dict) and
                    result.get('choices') and
                    len(result['choices']) > 0):

                content = result['choices'][0].get('message', {}).get('content', '')
                if self.expected_response in content:
                    self._update_status(ClientStatus.AVAILABLE)
                    self.error_count = 0
                    return True

            self._update_status(ClientStatus.ERROR)
            self.error_count += 1

        except Exception as e:
            logger.warning(f"Client test failed for {self.__class__.__name__}: {e}")
            self._update_status(ClientStatus.ERROR)
            self.error_count += 1

        self.last_test_time = current_time

        # Check if client should be marked as unavailable
        if self.error_count >= self.max_errors:
            self._update_status(ClientStatus.UNAVAILABLE)

        return self.status == ClientStatus.AVAILABLE

    def _update_status(self, new_status: ClientStatus):
        """Update client status with thread safety."""
        with self._lock:
            old_status = self.status
            self.status = new_status
            self.last_checked = time.time()

            if old_status != new_status:
                logger.info(f"Client status changed from {old_status} to {new_status}")


class AIClientManager:
    """
    Management framework for AI clients with priority-based selection,
    health monitoring, and automatic client management.
    """

    def __init__(self, storage_backend=None):
        """
        Initialize client manager.

        Args:
            storage_backend: Storage backend for usage data (optional)
        """
        self.clients = []  # List of BaseAIClient instances
        self._lock = threading.RLock()
        self.storage_backend = storage_backend
        self.monitor_thread = None
        self.monitor_running = False
        self.monitor_interval = 60  # Check every minute

    def register_client(self, client: BaseAIClient):
        """Register a new AI client."""
        with self._lock:
            self.clients.append(client)
            # Sort by priority (lower number = higher priority)
            self.clients.sort(key=lambda x: x.priority)

    def get_available_client(self) -> Optional[BaseAIClient]:
        """
        Get an available client based on priority and status.

        Returns:
            BaseAIClient or None if no clients available
        """
        with self._lock:
            for client in self.clients:
                # Skip unavailable clients
                if client.status == ClientStatus.UNAVAILABLE:
                    continue

                # Test client if status is unknown or it's been a while
                if (client.status == ClientStatus.UNKNOWN or
                        (client.last_checked and
                         time.time() - client.last_checked > client.test_interval)):
                    client.test_and_update_status()

                # Try to acquire available client
                if (client.status == ClientStatus.AVAILABLE and
                        client.acquire()):
                    return client

            return None

    def start_monitoring(self):
        """Start background monitoring of client health."""
        if self.monitor_running:
            return

        self.monitor_running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("Started AI client monitoring")

    def stop_monitoring(self):
        """Stop background monitoring."""
        self.monitor_running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=10)
        logger.info("Stopped AI client monitoring")

    def _monitor_loop(self):
        """Background monitoring loop."""
        while self.monitor_running:
            try:
                self._check_client_health()
                self._cleanup_unavailable_clients()
                self._save_usage_data()
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")

            time.sleep(self.monitor_interval)

    def _check_client_health(self):
        """Check health of all non-busy clients."""
        with self._lock:
            for client in self.clients:
                if not client.is_busy():
                    client.test_and_update_status()

    def _cleanup_unavailable_clients(self):
        """Remove clients that are permanently unavailable."""
        with self._lock:
            # Keep clients that might recover (not UNAVAILABLE)
            self.clients = [client for client in self.clients
                            if client.status != ClientStatus.UNAVAILABLE]

    def _save_usage_data(self):
        """Save usage data to storage backend."""
        if not self.storage_backend:
            return

        try:
            usage_data = {}
            for client in self.clients:
                usage_data[client.api_token] = {
                    "usage_stats": client.get_usage_stats(),
                    "last_checked": client.last_checked,
                    "status": client.status.value
                }

            self.storage_backend.save_usage_data(usage_data)
        except Exception as e:
            logger.error(f"Failed to save usage data: {e}")

    def get_client_stats(self) -> Dict[str, Any]:
        """Get statistics about all clients."""
        with self._lock:
            stats = {
                "total_clients": len(self.clients),
                "available_clients": len([c for c in self.clients
                                          if c.status == ClientStatus.AVAILABLE]),
                "unavailable_clients": len([c for c in self.clients
                                            if c.status == ClientStatus.UNAVAILABLE]),
                "clients": []
            }

            for client in self.clients:
                stats["clients"].append({
                    "type": client.__class__.__name__,
                    "status": client.status.value,
                    "priority": client.priority,
                    "usage_stats": client.get_usage_stats(),
                    "usage_metrics": client.get_usage_metrics(),
                    "last_checked": client.last_checked,
                    "is_busy": client.is_busy()
                })

            return stats

    def release_client(self, client: BaseAIClient):
        """Release a client back to the pool."""
        client.release()
