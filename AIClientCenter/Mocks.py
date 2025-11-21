import time
import random
import uuid
import json
import threading
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Union

from enum import Enum


# 假设 BaseAIClient 和 logger 已经定义
# from your_module import BaseAIClient, logger, ClientStatus

# ==========================================
# 1. 配置与状态模型 (Data Models)
# 用于序列化和未来对接前端 API
# ==========================================

class MockErrorType(Enum):
    NONE = "none"
    TIMEOUT = "timeout"  # 模拟网络超时
    HTTP_500 = "http_500"  # 模拟服务器崩了
    HTTP_429 = "http_429"  # 模拟限流
    HTTP_401 = "http_401"  # 模拟Token无效
    CONTENT_FILTER = "filter"  # 模拟内容被过滤
    MALFORMED = "malformed"  # 模拟返回垃圾数据


@dataclass
class MockBehaviorConfig:
    """控制 Mock 行为的配置单"""
    # 基础延迟设置
    min_latency_ms: int = 200
    max_latency_ms: int = 800

    # 错误注入 (Chaos Engineering)
    error_rate: float = 0.0  # 0.0 - 1.0, 随机出错概率
    forced_error_type: MockErrorType = MockErrorType.NONE

    # 定量/定时炸弹
    fail_after_requests: int = -1  # 设置为 N，则在第 N 次请求时必挂
    fail_until_timestamp: float = 0  # 在此时间戳之前一直报错 (模拟服务宕机)

    # 配额模拟
    simulated_balance: float = 100.0  # 模拟余额
    cost_per_token: float = 0.0001

    # 响应内容控制
    fixed_response_content: Optional[str] = None  # 如果设置，总是返回这句话
    model_name: str = "mock-gpt-4-turbo"


@dataclass
class MockRuntimeStats:
    """运行时产生的统计数据"""
    total_calls: int = 0
    total_errors: int = 0
    total_tokens_consumed: int = 0
    total_cost_accrued: float = 0.0
    last_call_timestamp: float = 0.0
    history_logs: List[Dict] = field(default_factory=list)  # 简单的调用日志环形缓冲区


# ==========================================
# 2. Advanced Mock Client
# ==========================================

class AdvancedMockAIClient(BaseAIClient):
    """
    一个支持遥控、混沌测试和详细遥测的高级 Mock 客户端。
    """

    def __init__(self, name: str, priority: int, config: MockBehaviorConfig = None):
        super().__init__(name, f"mock-token-{uuid.uuid4()}", priority)

        # 配置和状态
        self.config = config if config else MockBehaviorConfig()
        self.stats = MockRuntimeStats()

        # 专用的锁，防止修改配置时发生冲突
        self._config_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Remote Control Interfaces (遥控接口)
    # 这些方法未来可以映射到 REST API (e.g., POST /clients/{name}/config)
    # ------------------------------------------------------------------

    def update_behavior(self, **kwargs):
        """动态更新行为配置"""
        with self._config_lock:
            for k, v in kwargs.items():
                if hasattr(self.config, k):
                    # 如果是枚举，尝试转换
                    if k == 'forced_error_type' and isinstance(v, str):
                        v = MockErrorType(v)
                    setattr(self.config, k, v)
            logger.info(f"[{self.name}] Behavior updated: {kwargs}")

    def get_telemetry(self) -> Dict[str, Any]:
        """获取当前快照：配置 + 统计"""
        with self._config_lock:
            return {
                "name": self.name,
                "priority": self.priority,
                "status": self.get_status(),
                "config": asdict(self.config),
                "stats": asdict(self.stats)
            }

    def inject_chaos(self, error_type: str, duration_sec: int = 0):
        """一键注入故障（方便测试脚本调用）"""
        self.update_behavior(
            forced_error_type=error_type,
            fail_until_timestamp=time.time() + duration_sec if duration_sec > 0 else 0
        )

    # ------------------------------------------------------------------
    # Core Logic (BaseAIClient Implementations)
    # ------------------------------------------------------------------

    def _chat_completion_sync(self, messages, model=None, temperature=0.7, max_tokens=4096):
        """模拟真实的 API 处理流程"""
        request_id = str(uuid.uuid4())
        start_time = time.time()

        with self._config_lock:
            # 1. 模拟网络延迟
            latency = random.uniform(self.config.min_latency_ms, self.config.max_latency_ms) / 1000.0
            time.sleep(latency)

            # 2. 更新调用计数
            self.stats.total_calls += 1
            current_call_count = self.stats.total_calls

            # 3. 混沌与错误判断逻辑
            error_to_raise = self._determine_error_condition(current_call_count)

        # 4. 如果判定需要报错，则生成错误响应或抛出异常
        if error_to_raise != MockErrorType.NONE:
            self._record_log(request_id, "ERROR", error_to_raise.value, latency)
            return self._simulate_error_response(error_to_raise)

        # 5. 模拟正常响应
        # 计算虚拟 Token
        input_text = "".join([m['content'] for m in messages])
        prompt_tokens = len(input_text) // 4  # 粗略估算
        completion_content = self.config.fixed_response_content or \
                             f"Mock AI Response to '{input_text[:20]}...' [ID: {request_id}]"
        completion_tokens = len(completion_content) // 4
        total_tokens = prompt_tokens + completion_tokens

        # 计算虚拟成本
        cost = total_tokens * self.config.cost_per_token

        with self._config_lock:
            self.stats.total_tokens_consumed += total_tokens
            self.stats.total_cost_accrued += cost
            # 简单的余额扣除模拟
            self.config.simulated_balance -= cost

        self._record_log(request_id, "SUCCESS", "200_OK", latency)

        # 构造 OpenAI 兼容的 JSON
        return {
            "id": f"chatcmpl-{request_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.config.model_name,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": completion_content
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens
            }
        }

    def _determine_error_condition(self, call_count: int) -> MockErrorType:
        """决策引擎：判断当前是否应该报错"""
        # A. 时间炸弹 (Time Bomb)
        if 0 < self.config.fail_until_timestamp:
            if time.time() < self.config.fail_until_timestamp:
                return self.config.forced_error_type or MockErrorType.HTTP_500
            else:
                # 时间到了，自动解除
                self.config.fail_until_timestamp = 0
                self.config.forced_error_type = MockErrorType.NONE

        # B. 强制错误类型
        if self.config.forced_error_type != MockErrorType.NONE:
            return self.config.forced_error_type

        # C. 计数炸弹 (Count Bomb) - 第 N 次必挂
        if self.config.fail_after_requests == call_count:
            return MockErrorType.HTTP_500  # 默认爆一个500

        # D. 概率随机挂 (Random Chaos)
        if self.config.error_rate > 0:
            if random.random() < self.config.error_rate:
                return MockErrorType.HTTP_500

        return MockErrorType.NONE

    def _simulate_error_response(self, error_type: MockErrorType):
        """根据错误类型生成异常或返回错误JSON"""
        with self._config_lock:
            self.stats.total_errors += 1

        if error_type == MockErrorType.TIMEOUT:
            # 模拟 requests.exceptions.Timeout
            # 注意：需要在测试环境中 mock requests 或使用真实类
            import requests
            raise requests.exceptions.Timeout("Mocked ConnectTimeout")

        if error_type == MockErrorType.HTTP_500:
            # 模拟 HTTP 500 Response 对象
            mock_resp = type('MockResponse', (), {'status_code': 500, 'reason': 'Mock Internal Error'})
            return mock_resp

        if error_type == MockErrorType.HTTP_429:
            return {
                "error": {
                    "message": "Rate limit reached (Mock)",
                    "type": "rate_limit_exceeded",
                    "code": 429
                }
            }

        if error_type == MockErrorType.MALFORMED:
            return "<html>Not JSON</html>"

        return {"error": {"message": "Generic Mock Error"}}

    def _record_log(self, req_id, status, detail, latency):
        """记录最近的调用日志到内存环形缓冲区"""
        with self._config_lock:
            log_entry = {
                "id": req_id,
                "time": time.strftime("%H:%M:%S"),
                "status": status,
                "detail": detail,
                "latency_ms": int(latency * 1000)
            }
            self.stats.history_logs.insert(0, log_entry)
            # 只保留最近 50 条
            if len(self.stats.history_logs) > 50:
                self.stats.history_logs.pop()

    # ------------------------------------------------------------------
    # Interface Implementation
    # ------------------------------------------------------------------
    def get_usage_metrics(self) -> Dict[str, float]:
        # 计算剩余配额百分比
        # 假设初始配额是 100刀，当前余额在 config.simulated_balance
        initial = 100.0
        remaining = max(0, self.config.simulated_balance)
        pct = (remaining / initial) * 100.0
        return {"remaining_percentage": pct, "balance": remaining}

    def get_model_list(self) -> Dict[str, Any]:
        return {"data": [{"id": self.config.model_name}]}
