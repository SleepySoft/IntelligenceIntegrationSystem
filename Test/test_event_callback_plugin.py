from ServiceComponent.runtime import HubEvent, HubRuntime
from ServiceComponent.runtime.callbacks import EventCallbackPlugin


def test_callback_plugin_observes_event_without_mutating_payload():
    received = []
    payload = {"kind": "domain-specific"}
    runtime = HubRuntime()
    runtime.install(EventCallbackPlugin("archive.completed", [received.append]))
    runtime.start()

    runtime.emit(HubEvent("archive.completed", payload, subsystem="custom"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert received[0].payload is payload
    assert received[0].subsystem == "custom"


def test_callback_plugin_validates_configuration():
    try:
        EventCallbackPlugin("", [])
    except ValueError:
        pass
    else:
        raise AssertionError("空事件名必须被拒绝")

    try:
        EventCallbackPlugin("archive.completed", [object()])
    except TypeError:
        pass
    else:
        raise AssertionError("不可调用回调必须被拒绝")
