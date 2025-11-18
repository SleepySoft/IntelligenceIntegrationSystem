import time
import logging
import traceback

import requests
import datetime
import threading
from enum import Enum
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Union


logger = logging.getLogger(__name__)


CLIENT_PRIORITY_MOST_PRECIOUS = 0       # Precious API resource has the lowest using priority.
CLIENT_PRIORITY_EXPENSIVE = 30
CLIENT_PRIORITY_NORMAL = 50
CLIENT_PRIORITY_CONSUMABLES = 80
CLIENT_PRIORITY_FREEBIE = 100           # Prioritize using the regularly reset free quota

CLIENT_PRIORITY_HIGHER = 5
CLIENT_PRIORITY_LOWER = -5

CLIENT_PRIORITY_MORE_PRECIOUS = CLIENT_PRIORITY_LOWER
CLIENT_PRIORITY_LESS_PRECIOUS = CLIENT_PRIORITY_HIGHER


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

    def __init__(self, name: str, api_token: str, priority: int = CLIENT_PRIORITY_NORMAL):
        """
        Initialize AI client with token and priority.

        Args:
            api_token: API token for authentication
            priority: Client priority (lower number = higher priority)
        """
        self.name = name
        self.api_token = api_token
        self.priority = priority

        self._lock = threading.RLock()
        self._status = {
            'status': ClientStatus.UNKNOWN,
            'status_last_updated': 0.0,
            'last_acquired': 0.0,
            'last_released': 0.0,
            'last_chat': 0.0,
            'last_test': 0.0,
            'error_count': 0,
            'in_use': False
        }

        self._usage_stats = {
            "last_update": 0.0
        }

        self.test_prompt = "If you are working, please respond with 'OK'."
        self.expected_response = "OK"

    def chat(self,
             messages: List[Dict[str, str]],
             model: Optional[str] = None,
             temperature: float = 0.7,
             max_tokens: int = 4096) -> Dict[str, Any]:

        if self.status == ClientStatus.UNAVAILABLE:
            return {'error': 'client_unavailable', 'message': 'Client is marked as unavailable.'}

        try:
            response = self._chat_completion_sync(messages, model, temperature, max_tokens)

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

    def get_status(self, key: Optional[str] = None) -> Any:
        with self._lock:
            return self._status.get(key, None) if key else self._status.copy()

    def record_usage(self, usages: dict):
        """Record usage statistics."""
        with self._lock:
            self._usage_stats.update(usages)
            self._usage_stats["last_update"] = time.time()

    def get_usage_stats(self) -> Dict[str, Any]:
        """Get usage statistics."""
        with self._lock:
            return self._usage_stats.copy()

    # ------------------------------------------------------------------------------------------------------------------

    def _acquire(self) -> bool:
        """
        Attempt to acquire the client for use.

        Returns:
            bool: True if acquired successfully
        """
        with self._lock:
            if self._status['in_use'] or self._status['status'] == ClientStatus.UNAVAILABLE:
                return False

            self._status['in_use'] = True
            self._status['last_acquired'] = time.time()
            return True

    def _release(self):
        """Release the client after use."""
        with self._lock:
            self._status['in_use'] = False
            self._status['last_released'] = time.time()

    def _is_busy(self) -> bool:
        """Check if client is currently in use."""
        with self._lock:
            return self._status['in_use']

    def _test_and_update_status(self) -> bool:
        """
        Test client connectivity and update status.
        Called periodically by the management framework.

        Returns:
            bool: True if test was successfully completed.
        """
        if not self._acquire():
            return False

        try:
            result = self.chat(
                messages=[{"role": "user", "content": self.test_prompt}],
                max_tokens=100
            )

            if (isinstance(result, dict) and
                    result.get('choices') and
                    len(result['choices']) > 0):

                content = result['choices'][0].get('message', {}).get('content', '')
                if self.expected_response in content:
                    self._reset_error_count()
                    self._update_client_status(ClientStatus.AVAILABLE)
                    return True

            self._increase_error_count()
            self._update_client_status(ClientStatus.ERROR)

        except Exception as e:
            logger.warning(f"Client test failed for {self.name}: {e}")
            print(traceback.format_exc())
            self._increase_error_count()
            self._update_client_status(ClientStatus.ERROR)
        finally:
            self._status['last_test'] = time.time()

        return False

    def _reset_error_count(self):
        with self._lock:
            self._status['error_count'] = 0

    def _increase_error_count(self):
        with self._lock:
            self._status['error_count'] += 1

    def _update_client_status(self, new_status: ClientStatus):
        """Update client status with thread safety."""
        with self._lock:
            old_status = self._status['status']
            self._status['status'] = new_status
            self._status['status_last_updated'] = time.time()

            if old_status != new_status:
                logger.info(f"Client status changed from {old_status} to {new_status}")

    def _handle_http_error(self, response) -> Dict[str, Any]:
        """处理HTTP错误状态码"""
        # 根据状态码分类错误类型
        if response.status_code in [400, 422]:
            # 错误请求 - 通常是参数错误，可能是可恢复的
            error_type = 'recoverable'
            logger.warning(f"Bad request error (recoverable): {response.status_code}")

        elif response.status_code == 401:
            # 认证失败 - 通常是不可恢复的致命错误
            error_type = 'fatal'
            logger.error("Authentication failed - invalid API token")

        elif response.status_code == 403:
            # 权限不足 - 可能是不可恢复的
            error_type = 'fatal'
            logger.error("Permission denied - check API permissions")

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

        else:
            # 其他HTTP错误
            error_type = 'recoverable'
            logger.warning(f"HTTP error {response.status_code}")

        if error_type == 'recoverable':
            self._update_client_status(ClientStatus.ERROR)
        elif error_type == 'fatal':
            self._update_client_status(ClientStatus.UNAVAILABLE)
        self._increase_error_count()

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
        elif error_type in recoverable_errors:
            error_category = 'recoverable'
            logger.warning(f"Recoverable API error: {error_message}")
        else:
            error_category = 'recoverable'
            logger.warning(f"Unknown API error type: {error_type}, message: {error_message}")

        if error_type == 'recoverable':
            self._update_client_status(ClientStatus.ERROR)
        elif error_type == 'fatal':
            self._update_client_status(ClientStatus.UNAVAILABLE)
        self._increase_error_count()

        return {
            'error': 'api_error',
            'error_type': error_category,
            'api_error_type': error_type,
            'message': error_message
        }

    def _handle_exception(self, exception: Exception) -> Dict[str, Any]:
        """处理异常情况"""
        error_message = str(exception)

        # 根据异常类型分类
        if isinstance(exception, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            # 网络相关异常 - 通常是可恢复的
            error_type = 'recoverable'
            logger.warning(f"Network error (recoverable): {error_message}")

        elif isinstance(exception, requests.exceptions.RequestException):
            # 其他请求异常
            error_type = 'recoverable'
            logger.warning(f"Request exception: {error_message}")

        elif isinstance(exception, (ValueError, TypeError)):
            # 参数错误 - 可能是不可恢复的编程错误
            error_type = 'fatal'
            logger.error(f"Parameter error (fatal): {error_message}")

        else:
            # 其他未知异常
            error_type = 'recoverable'
            logger.error(f"Unexpected error: {error_message}")

        if error_type == 'recoverable':
            self._update_client_status(ClientStatus.ERROR)
        elif error_type == 'fatal':
            self._update_client_status(ClientStatus.UNAVAILABLE)
        self._increase_error_count()

        return {
            'error': 'exception',
            'error_type': error_type,
            'exception_type': type(exception).__name__,
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
                self._increase_error_count()

                return {
                    'error': 'llm_generation_issue',
                    'error_type': error_type,
                    'finish_reason': finish_reason,
                    'message': f"Response generation issue: {finish_reason}",
                    'choices': choices  # 仍然返回部分结果
                }

            try:
                # 统计token使用量
                if usage_data := response.get('usage', {}):
                    self._record_token_usage(usage_data, original_messages)
            except Exception as e:
                # Maybe not support.
                pass

            # 重置错误计数（成功请求）
            self._reset_error_count()
            self._update_client_status(ClientStatus.AVAILABLE)

            return response

        except Exception as e:
            logger.error(f"Error processing LLM response: {e}")
            return {
                'error': 'response_processing_error',
                'error_type': 'recoverable',
                'message': f'Failed to process LLM response: {str(e)}'
            }

    def _record_token_usage(self, usage_data: Dict[str, Any], original_messages: List[Dict[str, str]]):
        """记录token使用统计"""
        # 更新使用统计
        current_stats = {
            'prompt_tokens': usage_data.get('prompt_tokens', 0),
            'completion_tokens': usage_data.get('completion_tokens', 0),
            'total_tokens': usage_data.get('total_tokens', 0),
            'message_count': len(original_messages),
            'last_update': datetime.datetime.now()
        }

        with self._lock:
            # 合并历史统计
            self._usage_stats.update(current_stats)

            # 计算累计值
            if 'total_prompt_tokens' not in self._usage_stats:
                self._usage_stats['total_prompt_tokens'] = 0
            if 'total_completion_tokens' not in self._usage_stats:
                self._usage_stats['total_completion_tokens'] = 0

            self._usage_stats['total_prompt_tokens'] += current_stats['prompt_tokens']
            self._usage_stats['total_completion_tokens'] += current_stats['completion_tokens']

    # ------------------------------------------------- Abstractmethod -------------------------------------------------

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
    def _chat_completion_sync(self,
                              messages: List[Dict[str, str]],
                              model: Optional[str] = None,
                              temperature: float = 0.7,
                              max_tokens: int = 4096) -> Union[Dict[str, Any], requests.Response]:
        pass


# ----------------------------------------------------------------------------------------------------------------------

class AIClientManager:
    """
    Management framework for AI clients with priority-based selection,
    health monitoring, and automatic client management.
    """

    def __init__(self, base_check_interval_sec: int = 60):
        """
        Initialize client manager.
        """
        self.clients = []  # List of BaseAIClient instances
        self._lock = threading.RLock()
        self.monitor_thread = None
        self.monitor_running = False
        self.check_error_interval = base_check_interval_sec             # Check interval when client status is error.
        self.check_stable_interval = base_check_interval_sec * 10       # Check interval when client status is normal.

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

                # Try to acquire available client
                if client.status == ClientStatus.AVAILABLE and client._acquire():
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
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")

            time.sleep(self.monitor_interval)

    def _check_client_health(self):
        """Check health of all non-busy clients."""
        client_to_be_check = []

        with self._lock:
            for client in self.clients:
                if client._is_busy():
                    continue
                if client.status == ClientStatus.UNAVAILABLE:
                    continue

                usage_stats = client.get_usage_stats()
                last_update = usage_stats.get("last_update", 0.0)

                timeout = self.check_stable_interval \
                    if client.status == ClientStatus.AVAILABLE else \
                    self.check_error_interval

                if time.time() - last_update > timeout:
                    client_to_be_check.append(client)

        for client in client_to_be_check:
            logger.info(f'Checking client {client.name} status.')
            client._test_and_update_status()

    def _calculate_client_health(self, metrics: List[Dict[str, Any]]) -> float:
        """
        统一计算客户端的抽象健康评分（0-100）。
        选取最差（最低）的指标作为最终分数。
        """
        lowest_score = 100.0

        for m in metrics:
            metrics_type = m.get("metrics_type")
            current_score = 100.0

            if metrics_type in ["TOKEN_QUOTA", "CALL_COUNT"] and m.get("limit"):
                # 1. 配额/限次计算：基于百分比
                limit = m["limit"]
                usage = m["usage"]
                current_score = max(0, 100 * (limit - usage) / limit)

            elif metrics_type == "BALANCE" and m.get("current_value") is not None:
                # 2. 余额计算：基于硬性阈值
                balance = m["current_value"]
                threshold = m.get("hard_threshold", 0.0)

                if balance <= threshold:
                    current_score = 0.0  # 达到阈值，立即视为不可用
                else:
                    # 距离阈值越近，分数越低（例如：使用线性或对数衰减）
                    # 简单示例：如果余额是阈值的两倍以上，则认为健康
                    current_score = min(100.0, 100.0 * (balance - threshold) / threshold)

            # 如果有时间限制，也可以根据重置时间做惩罚
            # ...

            lowest_score = min(lowest_score, current_score)

        return lowest_score

    def _cleanup_unavailable_clients(self):
        """Remove clients that are permanently unavailable."""
        with self._lock:
            # Keep clients that might recover (not UNAVAILABLE)
            self.clients = [client for client in self.clients
                            if client.status != ClientStatus.UNAVAILABLE]

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
                    "last_checked": client.status_last_updated,
                    "is_busy": client._is_busy()
                })

            return stats

    def release_client(self, client: BaseAIClient):
        """Release a client back to the pool."""
        client._release()
