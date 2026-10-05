import threading

from ServiceComponent.runtime import HandlerFailure, HubEvent, HubPlugin, HubRuntime


def test_runtime_routes_opaque_payload_and_preserves_subsystem():
    runtime = HubRuntime()
    received = []
    runtime.subscribe("collected", lambda event, _: received.append(event))

    runtime.start()
    runtime.emit(HubEvent("collected", payload={"ticker": ["ABC"]}, subsystem="finance"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert received[0].payload == {"ticker": ["ABC"]}
    assert received[0].subsystem == "finance"
    assert runtime.stats["pending_events"] == 0
    assert runtime.stats["active_handlers"] == 0


def test_runtime_allows_handlers_to_publish_the_next_stage():
    runtime = HubRuntime()
    archived = []

    def analyze(event, hub):
        hub.emit(event.derive("archived", payload={"custom": event.payload}))

    runtime.subscribe("analysis_requested", analyze)
    runtime.subscribe("archived", lambda event, _: archived.append(event.payload))
    runtime.start()
    runtime.emit(HubEvent("analysis_requested", payload={"free_form": True}, subsystem="industry"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert archived == [{"custom": {"free_form": True}}]


def test_runtime_isolates_handler_failures_and_emits_failure_event():
    runtime = HubRuntime()
    handled = []
    failures = []

    def broken_handler(event, hub):
        raise RuntimeError("expected test failure")

    runtime.subscribe("work", broken_handler)
    runtime.subscribe("work", lambda event, _: handled.append(event.event_id))
    runtime.subscribe("runtime.handler_failed", lambda event, _: failures.append(event.payload))
    runtime.start()
    runtime.emit(HubEvent("work"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert len(handled) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], HandlerFailure)
    assert runtime.stats["handler_failures"] == 1


def test_runtime_plugin_lifecycle_and_drain_are_deterministic():
    calls = []
    completed = threading.Event()

    class TestPlugin(HubPlugin):
        def register(self, runtime):
            runtime.subscribe("work", self.handle)

        def start(self, runtime):
            calls.append("start")

        def stop(self, runtime):
            calls.append("stop")

        def handle(self, event, runtime):
            calls.append(event.payload)
            completed.set()

    runtime = HubRuntime()
    runtime.install(TestPlugin())
    runtime.emit(HubEvent("work", payload="handled-before-start"))
    runtime.start()

    assert completed.wait(timeout=1)
    runtime.stop()
    assert calls == ["start", "handled-before-start", "stop"]


def test_stop_with_drain_allows_a_handler_to_emit_its_next_stage():
    runtime = HubRuntime()
    archived = []

    def stage_one(event, hub):
        hub.emit(event.derive("stage_two", payload="kept"))

    runtime.subscribe("stage_one", stage_one)
    runtime.subscribe("stage_two", lambda event, _: archived.append(event.payload))
    runtime.start()
    runtime.emit(HubEvent("stage_one"))

    runtime.stop(drain=True)
    assert archived == ["kept"]
