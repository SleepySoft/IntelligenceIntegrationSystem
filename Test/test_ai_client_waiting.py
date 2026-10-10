import logging
from types import SimpleNamespace

from ServiceComponent.adapters.base_pipeline import BasePipelinePorts


class TimeoutThenAvailableManager:
    def __init__(self):
        self.calls = 0
        self.client = object()
        self.notified = False

    def wait_for_available_client(self, user_name, **kwargs):
        self.calls += 1
        return None if self.calls == 1 else self.client

    def get_scheduling_capacity(self, target_group_id=None):
        return 1

    def notify_waiters(self):
        self.notified = True


def build_ports(manager):
    return BasePipelinePorts(
        subsystem_registry=object(),
        ai_client_manager=manager,
        analyzer=lambda *_: {},
        transient_analyzer=lambda *_: {},
        scorer_factory=lambda _: object(),
        client_wait_timeout=60,
        analysis_worker_count=2,
    )


def test_wait_timeout_reports_worker_capacity_mismatch(caplog):
    manager = TimeoutThenAvailableManager()
    ports = build_ports(manager)

    with caplog.at_level(logging.WARNING):
        selected = ports._wait_for_ai_client(
            SimpleNamespace(ai_client_group="agent_cli"), "worker-2")

    assert selected is manager.client
    assert manager.calls == 2
    assert "analysis workers=2" in caplog.text
    assert "schedulable client capacity=1" in caplog.text


def test_stop_wakes_blocked_client_waiters():
    manager = TimeoutThenAvailableManager()
    ports = build_ports(manager)

    ports.stop()

    assert manager.notified is True
