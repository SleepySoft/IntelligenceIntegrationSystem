import time

from ServiceComponent.runtime import DeferredServicePlugin, HubRuntime


def test_deferred_service_waits_for_dependency_and_proxies_ready_service():
    available = [False]
    ready = []
    plugin = DeferredServicePlugin(
        lambda: available[0], lambda: {"created": True}, retry_interval=0.01, name="test",
        on_ready=ready.append)
    runtime = HubRuntime()
    runtime.install(plugin)
    runtime.start()
    assert not plugin

    available[0] = True
    assert _wait_until(lambda: plugin.ready)
    assert plugin.service == {"created": True}
    assert ready == [{"created": True}]
    runtime.stop()


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
