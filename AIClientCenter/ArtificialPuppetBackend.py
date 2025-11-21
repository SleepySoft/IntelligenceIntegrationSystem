import os
import threading
import time
import random
import traceback
import uuid
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Any

from flask import Flask, jsonify, request, render_template

self_path = os.path.dirname(os.path.abspath(__file__))

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AI-Emulator")


# =============================================================================
# 1. Models & Enums
# =============================================================================

class ClientStatus(Enum):
    AVAILABLE = "available"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class MockErrorType(str, Enum):
    NONE = "none"
    TIMEOUT = "timeout"
    HTTP_500 = "http_500"
    HTTP_429 = "http_429"
    MALFORMED = "malformed"


@dataclass
class MockBehaviorConfig:
    min_latency_ms: int = 200
    max_latency_ms: int = 800
    error_rate: float = 0.0
    forced_error_type: MockErrorType = MockErrorType.NONE
    simulated_balance: float = 100.0
    cost_per_token: float = 0.001
    fixed_response: Optional[str] = None
    fail_until_timestamp: float = 0.0  # 时间炸弹：在此时间戳之前一直报错
    fail_after_requests: int = -1  # 计数炸弹：在第 N 次请求时报错


@dataclass
class MockRuntimeStats:
    total_calls: int = 0
    total_errors: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    history_logs: List[Dict] = field(default_factory=list)


# =============================================================================
# 2. Advanced Mock Client
# =============================================================================

class AdvancedMockAIClient:
    def __init__(self, name: str, priority: int, config: MockBehaviorConfig = None):
        self.name = name
        self.priority = priority
        self.config = config if config else MockBehaviorConfig()
        self.stats = MockRuntimeStats()
        self._lock = threading.RLock()

        # Internal status tracking
        self._status = ClientStatus.AVAILABLE
        self._in_use = False
        self._acquired_by = None

    def chat(self, messages: List[Dict], **kwargs) -> Dict:
        """Simulates the API call with configured latency and errors."""
        req_id = str(uuid.uuid4())[:8]
        start_ts = time.time()

        # 1. Simulate Latency
        latency = random.uniform(self.config.min_latency_ms, self.config.max_latency_ms) / 1000.0
        time.sleep(latency)

        with self._lock:
            self.stats.total_calls += 1
            current_error_type = self._determine_error()

        # 2. Handle Errors
        if current_error_type != MockErrorType.NONE:
            with self._lock:
                self.stats.total_errors += 1
                self._log_history(req_id, "ERROR", current_error_type.value, latency)

            if current_error_type == MockErrorType.TIMEOUT:
                # In real life this raises exception, here we return error dict for simplicity
                return {"error": "timeout", "message": "Simulated Timeout"}
            elif current_error_type == MockErrorType.HTTP_429:
                return {"error": "rate_limit", "message": "Simulated Rate Limit"}
            return {"error": "server_error", "message": "Simulated 500 Error"}

        # 3. Successful Response
        input_len = sum(len(m.get('content', '')) for m in messages)
        prompt_tokens = input_len // 4
        completion_text = self.config.fixed_response or f"Simulated response from {self.name} (ID: {req_id})"
        completion_tokens = len(completion_text) // 4
        total_tokens = prompt_tokens + completion_tokens
        cost = total_tokens * self.config.cost_per_token

        with self._lock:
            self.stats.total_tokens += total_tokens
            self.stats.total_cost += cost
            self.config.simulated_balance -= cost
            self._log_history(req_id, "SUCCESS", "200 OK", latency)

        return {
            "id": req_id,
            "model": "mock-model",
            "choices": [{"message": {"role": "assistant", "content": completion_text}}],
            "usage": {"total_tokens": total_tokens}
        }

    def _determine_error(self) -> MockErrorType:
        # A. 优先级最高：时间炸弹
        if self.config.fail_until_timestamp > 0:
            if time.time() < self.config.fail_until_timestamp:
                # 在截止时间前，返回 503 Service Unavailable 或 500
                return MockErrorType.HTTP_500
            else:
                # 时间已过，拆除炸弹（自动恢复）
                self.config.fail_until_timestamp = 0.0

        # B. 优先级次之：计数炸弹
        # self.stats.total_calls 已经在调用此方法前 +1 了
        if self.config.fail_after_requests > 0:
            if self.stats.total_calls == self.config.fail_after_requests:
                # 命中炸弹，触发一次性错误
                return MockErrorType.HTTP_500

        # C. 强制错误类型
        if self.config.forced_error_type != MockErrorType.NONE:
            return self.config.forced_error_type

        # D. 随机错误
        if self.config.error_rate > 0 and random.random() < self.config.error_rate:
            return MockErrorType.HTTP_500

        return MockErrorType.NONE

    def _log_history(self, req_id, status, detail, latency):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "id": req_id,
            "status": status,
            "detail": detail,
            "latency": f"{int(latency * 1000)}ms"
        }
        self.stats.history_logs.insert(0, entry)
        if len(self.stats.history_logs) > 10:
            self.stats.history_logs.pop()

    # --- Management Interfaces ---
    def get_status(self, key=None):
        # Simplified for the emulator
        return self._status

    def _is_busy(self):
        return self._in_use

    def _acquire(self):
        with self._lock:
            if self._in_use or self._status == ClientStatus.UNAVAILABLE: return False
            self._in_use = True
            return True

    def _release(self):
        with self._lock:
            self._in_use = False

    def get_telemetry(self):
        with self._lock:
            return {
                "name": self.name,
                "priority": self.priority,
                "status": self._status.value,
                "in_use": self._in_use,
                "config": asdict(self.config),
                "stats": asdict(self.stats)
            }

    def update_config(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if k == 'forced_error_type': v = MockErrorType(v)
                if hasattr(self.config, k): setattr(self.config, k, v)


# =============================================================================
# 3. Simplified Manager (With User Affinity)
# =============================================================================

class AIClientManager:
    def __init__(self):
        self.clients = []
        self.user_map = {}  # {user: {'client': client_obj, 'ts': time}}

    def register_client(self, client):
        self.clients.append(client)
        self.clients.sort(key=lambda x: x.priority)

    def get_available_client(self, user_name: str):
        # 1. Check existing allocation
        if user_name in self.user_map:
            mapping = self.user_map[user_name]
            client = mapping['client']
            # If existing client is good, keep it (Sticky)
            # In a real implementation, we'd check for upgrades here
            if not client._is_busy():
                client._acquire()
                mapping['ts'] = time.time()
                return client

        # 2. Find new client
        for client in self.clients:
            if not client._is_busy() and client.get_status() != ClientStatus.UNAVAILABLE:
                if client._acquire():
                    # Release old if exists
                    if user_name in self.user_map:
                        self.user_map[user_name]['client']._release()

                    self.user_map[user_name] = {'client': client, 'ts': time.time()}
                    return client
        return None

    def release_client(self, user_name: str):
        if user_name in self.user_map:
            self.user_map[user_name]['client']._release()
            # In this emulator, we don't delete the mapping immediately
            # to show the "sticky" effect in the UI, but we release the lock.


# =============================================================================
# 4. Flask Application
# =============================================================================

app = Flask(__name__)
manager = AIClientManager()

# Setup specific scenarios
c1 = AdvancedMockAIClient("Fast Prime (P10)", priority=10,
                          config=MockBehaviorConfig(min_latency_ms=50, max_latency_ms=150, cost_per_token=0.01))
c2 = AdvancedMockAIClient("Standard (P50)", priority=50,
                          config=MockBehaviorConfig(min_latency_ms=500, max_latency_ms=1000, cost_per_token=0.005))
c3 = AdvancedMockAIClient("Free Tier (P100)", priority=100,
                          config=MockBehaviorConfig(min_latency_ms=1500, max_latency_ms=3000, cost_per_token=0.0))

manager.register_client(c1)
manager.register_client(c2)
manager.register_client(c3)


@app.route('/')
def index():
    return render_template(os.path.join(self_path, 'ArtificialPuppetFrontend.html'))


@app.route('/api/clients', methods=['GET'])
def get_clients():
    data = [c.get_telemetry() for c in manager.clients]
    # Add user info to the response
    for client_data in data:
        # Find which user holds this client
        holders = [u for u, m in manager.user_map.items() if m['client'].name == client_data['name']]
        client_data['held_by'] = holders[0] if holders else None
    return jsonify(data)


@app.route('/api/clients/<name>/config', methods=['POST'])
def update_client_config(name):
    client = next((c for c in manager.clients if c.name == name), None)
    if not client: return jsonify({"error": "Not found"}), 404

    data = request.json
    # Clean data types
    if 'min_latency_ms' in data: data['min_latency_ms'] = int(data['min_latency_ms'])
    if 'max_latency_ms' in data: data['max_latency_ms'] = int(data['max_latency_ms'])
    if 'error_rate' in data: data['error_rate'] = float(data['error_rate'])

    client.update_config(**data)
    return jsonify({"status": "updated"})


@app.route('/api/chat', methods=['POST'])
def test_chat():
    user = request.json.get('user', 'test_user')
    prompt = request.json.get('prompt', 'Hello')

    client = manager.get_available_client(user)
    if not client:
        return jsonify({"error": "No clients available"}), 503

    try:
        response = client.chat([{"role": "user", "content": prompt}])
        return jsonify({
            "client_used": client.name,
            "response": response
        })
    finally:
        # In real usage, we might not release immediately if streaming,
        # but here we release to allow other tests.
        manager.release_client(user)


if __name__ == '__main__':
    try:
        app.run(debug=True, use_reloader=False, port=9000)
    except Exception as e:
        print(str(e))
        print(traceback.format_exc())
