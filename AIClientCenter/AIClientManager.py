import time
import logging
import traceback
from collections import Counter

import requests
import datetime
import threading
from enum import Enum
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Union


logger = logging.getLogger(__name__)


CLIENT_PRIORITY_MOST_PRECIOUS = 100     # Precious API resource has the lowest using priority.
CLIENT_PRIORITY_EXPENSIVE = 80
CLIENT_PRIORITY_NORMAL = 50
CLIENT_PRIORITY_CONSUMABLES = 20
CLIENT_PRIORITY_FREEBIE = 0             # Prioritize using the regularly reset free quota

CLIENT_PRIORITY_HIGHER = -5
CLIENT_PRIORITY_LOWER = 5

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
    Base class for all AI clients.

    Capabilities:
    - Abstract interface for API calls.
    - Status management (Available/Busy/Error).

    Usage Tracking & Quotas:
    - This base class does NOT track token usage or limits.
    - To enable these features, your client class must inherit from `ClientMetricsMixin`.

    Example:
        class OpenAIClient(ClientMetricsMixin, BaseAIClient):
            def __init__(self, ...):
                ClientMetricsMixin.__init__(self, quota_config=...)
                BaseAIClient.__init__(self, ...)
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
            'in_use': False,
            'acquired': False
        }

        self.test_prompt = "If you are working, please respond with 'OK'."
        self.expected_response = "OK"

    # -------------------------------------- User interface --------------------------------------

    def chat(self,
             messages: List[Dict[str, str]],
             model: Optional[str] = None,
             temperature: float = 0.7,
             max_tokens: int = 4096) -> Dict[str, Any]:

        with self._lock:
            if self._status['status'] == ClientStatus.UNAVAILABLE:
                return {'error': 'client_unavailable', 'message': 'Client is marked as unavailable.'}
            if self._status['in_use']:
                return {'error': 'client_busy', 'message': 'Client is busy (in use).'}
            self._status['in_use'] = True

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

        finally:
            with self._lock:
                self._status['in_use'] = False

    def get_status(self, key: Optional[str] = None) -> Any:
        with self._lock:
            return self._status.get(key, None) if key else self._status.copy()

    # =========================================================================
    # Metrics & Health Interface (Stubs)
    # =========================================================================
    # Note: The BaseAIClient provides NO built-in statistics tracking.
    # To enable usage tracking, quotas, and balance checks, your subclass
    # must inherit from 'ClientMetricsMixin' alongside this base class.
    # =========================================================================

    def record_usage(self, usage_data: Dict[str, Any]):
        """
        Records usage statistics (e.g., tokens, cost) for this request.

        [STUB IMPLEMENTATION]
        By default, this method does nothing.

        To enable functionality:
            Inherit from `ClientMetricsMixin`. It will override this method to
            accumulate stats (using Counter) and trigger quota checks.

        Args:
            usage_data (Dict[str, Any]): A dictionary of usage deltas.
                Standard keys used by the Mixin include:
                - 'prompt_tokens' (int)
                - 'completion_tokens' (int)
                - 'total_tokens' (int)
                - 'cost_usd' (float)
        """
        # Intentionally left empty to serve as an interface.
        pass

    def calculate_health(self) -> float:
        """
        Calculates the abstract health score of the client (0.0 to 100.0).

        [STUB IMPLEMENTATION]
        Returns 100.0 (Fully Healthy) by default.

        To enable functionality:
            Inherit from `ClientMetricsMixin`. It will implement logic to return
            lower scores based on exhausted quotas or low balances.

        Returns:
            float: Always 100.0 unless overridden.
        """
        return 100.0

    def get_standardized_metrics(self) -> List[Dict[str, Any]]:
        """
        Retrieves standardized metric details for reporting and health calculation.
        Used by the Manager to display quota progress bars or balance alerts.

        [STUB IMPLEMENTATION]
        Returns an empty list by default.

        To enable functionality:
            Inherit from `ClientMetricsMixin`. It will return structured data like:
            [{'type': 'USAGE_LIMIT', 'current': 500, 'target': 1000}, ...]

        Returns:
            List[Dict]: Empty list unless overridden.
        """
        return []

    # ---------------------------------------- Not for user ----------------------------------------

    def _is_busy(self) -> bool:
        """Check if client is currently in use."""
        with self._lock:
            return self._status['in_use']

    def _acquire(self) -> bool:
        """
        Attempt to acquire the client for use.

        Returns:
            bool: True if acquired successfully
        """
        with self._lock:
            if self._status['acquired'] or self._status['status'] == ClientStatus.UNAVAILABLE:
                return False

            self._status['acquired'] = True
            self._status['last_acquired'] = time.time()
            return True

    def _release(self):
        """Release the client after use."""
        with self._lock:
            self._status['acquired'] = False
            self._status['last_released'] = time.time()

    def _is_acquired(self) -> bool:
        with self._lock:
            return self._status['acquired']

    def _test_and_update_status(self) -> bool:
        """
        Test client connectivity and update status.
        Called periodically by the management framework.

        Returns:
            bool: True if test was successfully completed.
        """
        try:
            result = self.chat(
                messages=[{"role": "user", "content": self.test_prompt}],
                max_tokens=100
            )
            if 'error' in result:
                return False

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
                logger.info(f"Client {self.name} status changed from {old_status} to {new_status}")

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
        # 1. 准备本次请求的增量数据
        # 注意：这里我们将 key 统一映射为想要的统计字段名
        increment_stats = Counter({
            'prompt_tokens': usage_data.get('prompt_tokens', 0),
            'completion_tokens': usage_data.get('completion_tokens', 0),
            'total_tokens': usage_data.get('total_tokens', 0),
            'message_count': len(original_messages),
            # # 如果你还需要专门保留带 'total_' 前缀的字段以兼容旧代码逻辑：
            # 'total_prompt_tokens': usage_data.get('prompt_tokens', 0),
            # 'total_completion_tokens': usage_data.get('completion_tokens', 0)
        })

        with self._lock:
            # 2. 自动累加
            # Counter.update() 会将 increment_stats 中的数值加到 self._usage_stats 上
            self._usage_stats.update(increment_stats)

            # 3. 单独处理非累加字段（时间戳需要覆盖，而不是相加）
            self._usage_stats['last_update'] = time.time()

    # ---------------------------------------- Abstractmethod ----------------------------------------

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

    def __init__(self, base_check_interval_sec: int = 60, first_check_delay_sec: int = 10):
        """
        Initialize client manager.

        Args:
            base_check_interval_sec: Base interval for health checks.
            first_check_delay_sec: Delay before the first check loop starts.
        """
        self.clients = []  # List of BaseAIClient instances
        self._lock = threading.RLock()
        self.monitor_thread = None
        self.monitor_running = False

        # Configuration for the monitoring loop
        self.check_error_interval = base_check_interval_sec  # Interval when client is in ERROR state
        self.check_stable_interval = base_check_interval_sec * 10  # Interval when client is AVAILABLE
        self.first_check_delay_sec = first_check_delay_sec

    def register_client(self, client: Any):
        """
        Register a new AI client.
        """
        with self._lock:
            self.clients.append(client)
            # Sort by priority (lower number = higher priority)
            # This ensures get_available_client always picks the best one first.
            self.clients.sort(key=lambda x: x.priority)
            logger.info(f"Registered client: {getattr(client, 'name', 'Unknown')}")

    def get_available_client(self) -> Optional[Any]:
        """
        Get an available client based on priority and status.

        Returns:
            BaseAIClient or None if no clients are available/healthy.
        """
        with self._lock:
            for client in self.clients:
                client_status = client.get_status('status')

                # 1. Filter out permanently dead clients
                if client_status == ClientStatus.UNAVAILABLE: continue

                # 2. Check dynamic health (Optional optimization: skip if health is 0)
                # If you want strict checking:
                # if client.calculate_health() <= 0: continue

                # 3. Try to acquire lock/token for the client
                if client_status == ClientStatus.AVAILABLE and client._acquire():
                    logger.info(f"Get client: {client.name}")
                    return client

            return None

    def release_client(self, client: Any):
        """Release a client back to the pool."""
        # Simple wrapper to release the lock/semaphore on the client
        if hasattr(client, '_release'):
            client._release()

    def get_client_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive statistics about all clients for dashboards/logs.
        Leverages the ClientMetricsMixin for detailed health data.
        """
        with self._lock:
            # Pre-calculate lists for summary
            available_list = [c for c in self.clients if c.get_status('status') == ClientStatus.AVAILABLE]
            unavailable_list = [c for c in self.clients if c.get_status('status') == ClientStatus.UNAVAILABLE]
            busy_list = [c for c in self.clients if c._is_busy()]

            client_details = []
            for client in self.clients:
                # 1. Get the calculated health score directly from the Mixin
                health_score = client.calculate_health()

                # 2. Get the standardized explanation of why the health is what it is
                metrics_detail = client.get_standardized_metrics()

                client_details.append({
                    "name": getattr(client, "name", "Unknown"),
                    "type": client.__class__.__name__,
                    "status": client.get_status('status'),
                    "priority": client.priority,
                    "health_score": health_score,  # 0-100 score
                    "is_busy": client._is_busy(),
                    "last_checked": client.get_status('status_last_updated'),
                    # Detailed breakdowns
                    "constraint_metrics": metrics_detail,  # Quota/Balance snapshots
                })

            # Sort details by priority (high priority first) then by health
            client_details.sort(key=lambda x: (x['priority'], -x['health_score']))

            return {
                "summary": {
                    "total": len(self.clients),
                    "available": len(available_list),
                    "unavailable": len(unavailable_list),
                    "busy": len(busy_list),
                    "timestamp": time.time()
                },
                "clients": client_details
            }

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
        logger.info("Monitor loop started.")
        while self.monitor_running:
            # Initial startup delay
            if self.first_check_delay_sec > 0:
                self.first_check_delay_sec -= 1
                time.sleep(1)
                continue

            try:
                self._check_client_health()
                self._cleanup_unavailable_clients()
            except Exception as e:
                # Prevent monitor thread from crashing entirely
                logger.error(f"Error in monitor loop: {e}")
                logger.debug(traceback.format_exc())

            # Sleep with a small deviation to avoid thundering herd if multiple managers exist
            time.sleep(self.check_error_interval)

    def _check_client_health(self):
        """
        Trigger active health checks (connectivity/latency) for eligible clients.
        This does NOT recalculate quota/balance logic (handled by Mixin internally).
        """
        clients_to_check = []

        with self._lock:
            for client in self.clients:
                client_status = client.get_status('status')
                if client_status == ClientStatus.UNAVAILABLE:
                    continue

                status_last_updated = client.get_status('status_last_updated')

                # Determine timeout based on current status
                timeout = self.check_stable_interval \
                    if client_status == ClientStatus.AVAILABLE else \
                    self.check_error_interval

                if time.time() - status_last_updated > timeout:
                    clients_to_check.append(client)

        # Perform checks outside the main lock to avoid blocking get_available_client
        for client in clients_to_check:
            client_name = getattr(client, 'name', 'Unknown Client')
            logger.debug(f'Checking connectivity for {client_name}...')

            # This method usually pings the API or checks simple connectivity
            result = client._test_and_update_status()

            if not result:
                logger.warning(f"Status check failed for {client_name}.")

    def _cleanup_unavailable_clients(self):
        """
        Remove clients that are marked as UNAVAILABLE (permanently dead).
        Clients with 0 Health (Quota exceeded) are usually kept as they might recover next month,
        unless the client logic explicitly sets them to UNAVAILABLE.
        """
        with self._lock:
            initial_count = len(self.clients)
            self.clients = [
                client for client in self.clients
                if client.get_status('status') != ClientStatus.UNAVAILABLE
            ]
            removed = initial_count - len(self.clients)
            if removed > 0:
                logger.info(f"Cleaned up {removed} unavailable clients.")
