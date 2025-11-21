import time
import pytest
from dataclasses import asdict
from unittest.mock import MagicMock, patch


from AIClientCenter.AIClientManager import AIClientManager, ClientStatus
from AIClientCenter.ArtificialPuppetBackend import AdvancedMockAIClient, MockBehaviorConfig, MockErrorType


# 测试覆盖率：
# pip install pytest-cov
# pytest --cov=ai_emulator


# =============================================================================
# Fixtures: 为测试准备干净的实例
# =============================================================================

@pytest.fixture
def mock_config():
    """返回一个基础的配置对象"""
    return MockBehaviorConfig(
        min_latency_ms=10,  # 测试时设短一点
        max_latency_ms=20,
        simulated_balance=100.0,
        cost_per_token=0.01
    )


@pytest.fixture
def client(mock_config):
    """返回一个初始化好的 Client"""
    return AdvancedMockAIClient("TestClient", priority=10, config=mock_config)


@pytest.fixture
def manager():
    """返回一个空的 Manager"""
    return AIClientManager()


# =============================================================================
# Part 1: AdvancedMockAIClient 核心功能测试
# =============================================================================

class TestMockClientCore:

    def test_basic_chat_success(self, client):
        """测试正常的对话流程：返回结构、Token计算、余额扣除"""
        initial_balance = client.config.simulated_balance

        # 发起请求
        response = client.chat([{"role": "user", "content": "Hello (5 chars)"}])

        # 1. 验证响应结构
        assert "choices" in response
        assert response["model"] == "mock-model"
        assert "usage" in response

        # 2. 验证统计数据更新
        assert client.stats.total_calls == 1
        assert client.stats.total_tokens > 0
        assert client.stats.total_cost > 0

        # 3. 验证余额扣除
        assert client.config.simulated_balance < initial_balance
        assert client.config.simulated_balance == initial_balance - client.stats.total_cost

    @patch('time.sleep')
    def test_latency_simulation(self, mock_sleep, client):
        """测试延迟模拟是否生效 (Mock掉sleep防止测试变慢)"""
        # 设置明确的延迟范围
        client.update_config(min_latency_ms=100, max_latency_ms=100)

        client.chat([{"role": "user", "content": "hi"}])

        # 验证 time.sleep 被调用，且参数约为 0.1秒
        mock_sleep.assert_called_once()
        args, _ = mock_sleep.call_args
        assert 0.09 <= args[0] <= 0.11

    def test_history_logging(self, client):
        """测试历史日志记录功能"""
        client.chat([{"role": "user", "content": "req1"}])
        client.chat([{"role": "user", "content": "req2"}])

        logs = client.stats.history_logs
        assert len(logs) == 2
        assert logs[0]['status'] == 'SUCCESS'  # 最新的在前面


# =============================================================================
# Part 2: Chaos Engineering (故障注入) 测试
# =============================================================================

class TestChaosFeatures:

    def test_forced_error_500(self, client):
        """测试强制注入 HTTP 500 错误"""
        client.update_config(forced_error_type=MockErrorType.HTTP_500)

        resp = client.chat([{"role": "user", "content": "hi"}])

        assert "error" in resp
        assert resp["error"] == "server_error"
        assert client.stats.total_errors == 1

        # 验证日志记录了错误
        assert client.stats.history_logs[0]['status'] == "ERROR"
        assert client.stats.history_logs[0]['detail'] == MockErrorType.HTTP_500.value

    def test_forced_timeout(self, client):
        """测试强制注入超时"""
        client.update_config(forced_error_type=MockErrorType.TIMEOUT)
        resp = client.chat([{"role": "user", "content": "hi"}])
        assert resp["error"] == "timeout"

    def test_random_error_rate(self, client):
        """测试随机错误率 (设置为 100% 确保必定失败)"""
        client.update_config(error_rate=1.0)  # 100% 错误

        resp = client.chat([{"role": "user", "content": "hi"}])

        # 注意：根据代码逻辑，random error 默认返回 HTTP 500
        assert "error" in resp or resp.get('status_code') == 500
        assert client.stats.total_errors == 1

    def test_runtime_config_update(self, client):
        """测试运行时动态修改配置"""
        # 1. 先是正常的
        resp1 = client.chat([{"role": "user", "content": "ok"}])
        assert "error" not in resp1

        # 2. 动态修改为强制 429
        client.update_config(forced_error_type='http_429')

        resp2 = client.chat([{"role": "user", "content": "fail"}])
        assert resp2["error"] == "rate_limit"


# =============================================================================
# Part 3: AIClientManager 调度逻辑测试
# =============================================================================

class TestManagerLogic:

    def test_priority_selection(self, manager):
        """测试：总是优先选择优先级高 (priority数值小) 的空闲 Client"""
        c_gold = AdvancedMockAIClient("Gold", priority=10)
        c_silver = AdvancedMockAIClient("Silver", priority=50)

        manager.register_client(c_silver)
        manager.register_client(c_gold)  # 乱序注册

        # 获取 Client
        selected = manager.get_available_client("user_a")

        assert selected.name == "Gold"
        assert selected._is_busy() is True

    def test_sticky_session(self, manager):
        """测试：用户粘性 - 同一个用户再次请求应获得同一个 Client"""
        c1 = AdvancedMockAIClient("C1", priority=50)
        c2 = AdvancedMockAIClient("C2", priority=50)

        manager.register_client(c1)
        manager.register_client(c2)

        # User A 第一次获得 C1
        client_first = manager.get_available_client("user_a")
        assert client_first == c1

        # 释放锁但保持映射 (模拟一次请求结束)
        manager.release_client("user_a")
        assert c1._is_busy() is False

        # User A 第二次请求，即使 C2 也是空的，也应该优先给 C1 (粘性)
        client_second = manager.get_available_client("user_a")
        assert client_second == c1

    def test_busy_handling(self, manager):
        """测试：高优先级忙碌时，自动降级"""
        c_gold = AdvancedMockAIClient("Gold", priority=10)
        c_silver = AdvancedMockAIClient("Silver", priority=50)
        manager.register_client(c_gold)
        manager.register_client(c_silver)

        # 模拟 Gold 被别人占用了
        c_gold._acquire()

        # User B 请求，应该得到 Silver
        client = manager.get_available_client("user_b")
        assert client.name == "Silver"

    def test_auto_upgrade(self, manager):
        """测试：自动升级 - 当高优先级从忙碌变为空闲，用户下次请求应切换过去"""
        c_gold = AdvancedMockAIClient("Gold", priority=10)
        c_silver = AdvancedMockAIClient("Silver", priority=50)
        manager.register_client(c_gold)
        manager.register_client(c_silver)

        # 1. Gold 忙碌
        c_gold._acquire()

        # 2. User A 只能拿到 Silver
        c_user = manager.get_available_client("user_a")
        assert c_user.name == "Silver"
        manager.release_client("user_a")  # 请求结束

        # 3. Gold 变为空闲
        c_gold._release()

        # 4. User A 再次请求 -> 应该自动升级到 Gold
        c_user_new = manager.get_available_client("user_a")
        assert c_user_new.name == "Gold"

        # 验证旧的 Silver 已经被释放
        assert c_silver._is_busy() is False

    def test_telemetry_aggregation(self, manager):
        """测试：Manager 能正确聚合所有 Client 的遥测数据"""
        c1 = AdvancedMockAIClient("C1", priority=10)
        manager.register_client(c1)

        # 模拟一次调用
        manager.get_available_client("u1").chat([{"role": "u", "content": "test"}])
        manager.release_client("u1")

        # 验证 API 格式的数据
        # 注意：这里的逻辑依赖于 server.py 中的 get_clients 实现逻辑
        # 我们手动模拟 server.py 里的行为
        telemetry_list = [c.get_telemetry() for c in manager.clients]

        assert len(telemetry_list) == 1
        assert telemetry_list[0]['stats']['total_calls'] == 1
        assert telemetry_list[0]['status'] == 'available'


# =============================================================================
# Part 4: 扩展特性 (Time Bomb & Count Bomb)
# 如果你在 AdvancedMockAIClient 中实现了 fail_until / fail_after，可以用这些测试
# =============================================================================

class TestAdvancedChaos:

    def test_time_bomb(self, client):
        """
        测试时间炸弹：
        设定一个未来时间点 T。
        在 T 之前请求 -> 失败。
        在 T 之后请求 -> 成功。
        """
        # 1. 设定炸弹：假设当前是 1000，设定 2000 之前都挂掉
        cutoff_time = 2000.0
        client.update_config(fail_until_timestamp=cutoff_time)

        # 2. Mock 时间：模拟现在是 1500 (炸弹生效期)
        # 注意：我们要 patch 你的代码文件中导入的 time 模块，而不是测试文件的 time
        with patch('AIClientTest.time.time') as mock_time:
            mock_time.return_value = 1500.0

            # 发起请求
            resp = client.chat([{"role": "user", "content": "test"}])

            # 断言：应该报错
            assert "error" in resp
            assert resp["error"] == "server_error"

        # 3. Mock 时间：模拟现在是 2500 (炸弹过期)
        with patch('AIClientTest.time.time') as mock_time:
            mock_time.return_value = 2500.0

            # 发起请求
            resp = client.chat([{"role": "user", "content": "test"}])

            # 断言：应该成功
            assert "error" not in resp

            # 断言：炸弹应该被自动拆除了 (fail_until_timestamp 重置为 0)
            assert client.config.fail_until_timestamp == 0.0

    def test_count_bomb(self, client):
        """
        测试计数炸弹：
        设定 fail_after_requests = 3。
        第 1, 2 次 -> 成功。
        第 3 次 -> 失败。
        第 4 次 -> 成功 (因为是一次性炸弹)。
        """
        # 设定第 3 次必挂
        client.update_config(fail_after_requests=3)

        # 第 1 次
        resp1 = client.chat([{"role": "user", "content": "1"}])
        assert "error" not in resp1

        # 第 2 次
        resp2 = client.chat([{"role": "user", "content": "2"}])
        assert "error" not in resp2

        # 第 3 次 (BOOM!)
        resp3 = client.chat([{"role": "user", "content": "3"}])
        assert "error" in resp3
        assert resp3["error"] == "server_error"

        # 第 4 次 (恢复正常)
        resp4 = client.chat([{"role": "user", "content": "4"}])
        assert "error" not in resp4

    def test_mixed_chaos_priority(self, client):
        """
        测试混合优先级：
        如果同时配置了 '强制错误' 和 '时间炸弹'，谁生效？
        (根据代码逻辑，时间炸弹优先级最高)
        """
        # 1. 设定时间炸弹 (生效中)
        client.update_config(fail_until_timestamp=9999999999)

        # 2. 设定强制错误为 Timeout
        client.update_config(forced_error_type='timeout')

        # 3. 执行
        # 代码逻辑中：时间炸弹 return HTTP_500，强制错误 return TIMEOUT
        # 我们看看到底返回什么
        with patch('AIClientTest.time.time') as mock_time:
            mock_time.return_value = 1000.0
            resp = client.chat([{"role": "user", "content": "test"}])

            # 根据我上面给出的 _determine_error 实现顺序，
            # 时间炸弹判定在最前面，所以应该返回 500 而不是 timeout
            assert resp["message"] == "Simulated 500 Error"

