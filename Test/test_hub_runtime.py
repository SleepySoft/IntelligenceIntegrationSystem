import threading

from ServiceComponent.runtime import (
    EventPriority,
    HandlerFailure,
    HubEvent,
    HubPlugin,
    HubRuntime,
)


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


def test_runtime_stats_break_down_queued_active_and_processed_event_types():
    runtime = HubRuntime(worker_count=1)
    entered = threading.Event()
    release = threading.Event()

    def hold(event, _):
        entered.set()
        release.wait(timeout=1)

    runtime.subscribe("analysis.requested", hold)
    runtime.start()
    runtime.emit(HubEvent("analysis.requested"))
    assert entered.wait(timeout=1)
    runtime.emit(HubEvent("archive.requested"))

    active_stats = runtime.stats
    assert active_stats["active_by_type"] == {"analysis.requested": 1}
    assert active_stats["queued_by_type"] == {"archive.requested": 1}
    assert active_stats["queued_by_lane"] == {"live": 1}

    release.set()
    assert runtime.wait_for_idle(timeout=1)
    final_stats = runtime.stats
    runtime.stop()

    assert final_stats["queued_by_type"] == {}
    assert final_stats["active_by_type"] == {}
    assert final_stats["processed_by_type"] == {
        "analysis.requested": 1,
        "archive.requested": 1,
    }


def test_runtime_prioritizes_live_events_over_replay_backlog():
    runtime = HubRuntime(worker_count=1)
    received = []
    runtime.subscribe("work", lambda event, _: received.append(event.payload))
    runtime.emit(HubEvent("work", "old-1"), priority=EventPriority.REPLAY)
    runtime.emit(HubEvent("work", "old-2"), priority=EventPriority.REPLAY)
    runtime.emit(HubEvent("work", "new"))

    runtime.start()
    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()

    assert received == ["new", "old-1", "old-2"]


def test_runtime_prioritizes_derived_stage_over_replay_backlog():
    runtime = HubRuntime(worker_count=1)
    received = []

    def analyze(event, hub):
        received.append(f"analysis:{event.payload}")
        hub.emit(event.derive("result", event.payload))

    runtime.subscribe("analysis", analyze)
    runtime.subscribe("result", lambda event, _: received.append(f"result:{event.payload}"))
    runtime.emit(HubEvent("analysis", "old-1"), priority=EventPriority.REPLAY)
    runtime.emit(HubEvent("analysis", "old-2"), priority=EventPriority.REPLAY)

    runtime.start()
    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()

    assert received == [
        "analysis:old-1", "result:old-1", "analysis:old-2", "result:old-2",
    ]
