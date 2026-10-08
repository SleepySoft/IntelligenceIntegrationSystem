from concurrent.futures import ThreadPoolExecutor

from tenacity import wait_none

from ServiceComponent.adapters import IISPipelinePorts
from ServiceComponent.manual_debug_analysis import MANUAL_TEST_SOURCE
from ServiceComponent.pipeline import (
    ANALYSIS_FAILED,
    ARCHIVE_COMPLETED,
    ARCHIVE_FAILED,
    ARCHIVE_REQUESTED,
    EventPipelinePlugin,
    INTAKE_RECEIVED,
    INTAKE_REJECTED,
)
from ServiceComponent.runtime import HubEvent, HubRuntime


class FakeStorage:
    def __init__(self):
        self.inserted = []
        self.updated = []

    def insert(self, data):
        self.inserted.append(dict(data))

    def update(self, query, update):
        self.updated.append((query, update))


class FakeQuery:
    def __init__(self, duplicated=False):
        self.duplicated = duplicated

    def common_query(self, **kwargs):
        return [{"UUID": "duplicate"}] if self.duplicated else []


class FakeContext:
    name = "finance"
    prompt_table = {1: "finance prompt"}
    scoring_config = None
    ai_client_group = None

    def __init__(self):
        self.mongo_db_cache = FakeStorage()
        self.mongo_db_archive = FakeStorage()
        self.mongo_db_low_value = FakeStorage()
        self.cache_query_engine = FakeQuery()
        self.archive_query_engine = FakeQuery()
        self.stats = {"archived": 0, "dropped": 0, "error": 0}


class FakeRegistry:
    def __init__(self, ctx):
        self.ctx = ctx

    def resolve(self, name):
        return self.ctx if name == "finance" else None

    def refresh_prompts(self, ctx):
        return ctx.prompt_table


class FakeClient:
    def get_api_base_url(self):
        return "fake://ai"

    def get_current_model(self):
        return "fake-model"


class FakeClientManager:
    def __init__(self):
        self.client = FakeClient()
        self.released = []

    def get_available_client(self, user):
        return self.client

    def release_client(self, client):
        self.released.append(client)


class FakeScorer:
    def calculate_single(self, data):
        return 7.5


def _original():
    return {
        "UUID": "item-1", "token": "collector", "title": "Finance headline",
        "content": "A sufficiently long finance story.", "informant": "https://example.test/a",
    }


def _analyzer(client, prompt, original):
    assert prompt == "finance prompt"
    return {
        "TAXONOMY": "Finance", "EVENT_TITLE": "Market event",
        "EVENT_BRIEF": "A market event", "EVENT_TEXT": "Detailed market analysis.",
        "RATE": {"新颖性与异常性": 8}, "APPENDIX": {"TICKER": ["ABC"]},
    }


def test_iis_adapter_runs_current_news_style_flow_without_runtime_dependencies():
    ctx = FakeContext()
    manager = FakeClientManager()
    ports = IISPipelinePorts(FakeRegistry(ctx), manager, analyzer=_analyzer,
                             scorer_factory=lambda _: FakeScorer())
    runtime = HubRuntime()
    runtime.install(EventPipelinePlugin(ports, ports, ports))
    archived = []
    runtime.subscribe(ARCHIVE_COMPLETED, lambda event, _: archived.append(event.payload))
    runtime.start()
    runtime.emit(HubEvent(INTAKE_RECEIVED, _original(), "finance"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert len(ctx.mongo_db_cache.inserted) == 1
    assert len(ctx.mongo_db_archive.inserted) == 1
    assert manager.released == [manager.client]
    assert archived[0]["APPENDIX"]["TICKER"] == ["ABC"]
    assert archived[0]["APPENDIX"]["__TOTAL_SCORE__"] == 7.5


def test_iis_adapter_keeps_low_value_result_out_of_archive():
    ctx = FakeContext()
    ports = IISPipelinePorts(
        FakeRegistry(ctx), FakeClientManager(),
        analyzer=lambda *_: {"TAXONOMY": "无情报价值"},
        scorer_factory=lambda _: FakeScorer(),
    )
    runtime = HubRuntime()
    runtime.install(EventPipelinePlugin(ports, ports, ports))
    rejected = []
    runtime.subscribe(INTAKE_REJECTED, lambda event, _: rejected.append(event.payload))
    runtime.start()
    runtime.emit(HubEvent(INTAKE_RECEIVED, _original(), "finance"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert not ctx.mongo_db_archive.inserted
    assert ctx.mongo_db_cache.updated
    assert len(ctx.mongo_db_low_value.inserted) == 1
    assert ctx.stats["dropped"] == 1


def test_iis_adapter_retries_transient_ai_errors_three_times():
    ctx = FakeContext()
    manager = FakeClientManager()
    calls = []

    def flaky_analyzer(*_):
        calls.append(1)
        if len(calls) < 3:
            return {"error": "temporary"}
        return _analyzer(*_)

    ports = IISPipelinePorts(
        FakeRegistry(ctx), manager, analyzer=flaky_analyzer,
        scorer_factory=lambda _: FakeScorer(), retry_wait=wait_none())
    runtime = HubRuntime()
    runtime.install(EventPipelinePlugin(ports, ports, ports))
    runtime.start()
    runtime.emit(HubEvent(INTAKE_RECEIVED, _original(), "finance"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert len(calls) == 3
    assert len(manager.released) == 3
    assert len(ctx.mongo_db_archive.inserted) == 1


def test_iis_adapter_marks_http_400_sensitive_without_retrying():
    ctx = FakeContext()
    calls = []

    def rejected_analyzer(*_):
        calls.append(1)
        return {"error": "bad request", "api_error_code": "HTTP_400"}

    ports = IISPipelinePorts(
        FakeRegistry(ctx), FakeClientManager(), analyzer=rejected_analyzer,
        scorer_factory=lambda _: FakeScorer(), retry_wait=wait_none())
    runtime = HubRuntime()
    runtime.install(EventPipelinePlugin(ports, ports, ports))
    failed = []
    runtime.subscribe(ANALYSIS_FAILED, lambda event, _: failed.append(event.payload))
    runtime.start()
    runtime.emit(HubEvent(INTAKE_RECEIVED, _original(), "finance"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert len(calls) == 1
    assert failed
    assert ctx.mongo_db_cache.updated[-1][1]["APPENDIX.__ARCHIVED__"] == "S"


def test_iis_adapter_marks_persistent_ai_error_after_three_attempts():
    ctx = FakeContext()
    calls = []

    def failed_analyzer(*_):
        calls.append(1)
        return {"error": "temporary but persistent"}

    ports = IISPipelinePorts(
        FakeRegistry(ctx), FakeClientManager(), analyzer=failed_analyzer,
        scorer_factory=lambda _: FakeScorer(), retry_wait=wait_none())
    runtime = HubRuntime()
    runtime.install(EventPipelinePlugin(ports, ports, ports))
    runtime.start()
    runtime.emit(HubEvent(INTAKE_RECEIVED, _original(), "finance"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert len(calls) == 3
    assert not ctx.mongo_db_archive.inserted
    assert ctx.mongo_db_cache.updated[-1][1]["APPENDIX.__ARCHIVED__"] == "E"


def test_iis_adapter_waits_until_an_ai_client_is_available():
    ctx = FakeContext()

    class DelayedClientManager(FakeClientManager):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def get_available_client(self, user):
            self.attempts += 1
            return self.client if self.attempts >= 3 else None

    manager = DelayedClientManager()
    ports = IISPipelinePorts(
        FakeRegistry(ctx), manager, analyzer=_analyzer,
        scorer_factory=lambda _: FakeScorer(), client_wait_interval=0.001)
    runtime = HubRuntime()
    runtime.install(EventPipelinePlugin(ports, ports, ports))
    runtime.start()
    runtime.emit(HubEvent(INTAKE_RECEIVED, _original(), "finance"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert manager.attempts == 3
    assert len(ctx.mongo_db_archive.inserted) == 1


def test_iis_adapter_rejects_invalid_direct_archive_payload():
    ctx = FakeContext()
    ports = IISPipelinePorts(
        FakeRegistry(ctx), FakeClientManager(), analyzer=_analyzer,
        scorer_factory=lambda _: FakeScorer())
    runtime = HubRuntime()
    runtime.install(EventPipelinePlugin(ports, ports, ports))
    failed = []
    runtime.subscribe(ARCHIVE_FAILED, lambda event, _: failed.append(event.payload))
    runtime.start()
    runtime.emit(HubEvent(
        ARCHIVE_REQUESTED,
        {"UUID": "bad", "INFORMANT": "source", "EVENT_TEXT": "looks valid"},
        "finance",
    ))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert failed
    assert not ctx.mongo_db_archive.inserted
    assert ctx.mongo_db_cache.updated[-1][1]["APPENDIX.__ARCHIVED__"] == "E"


def test_iis_adapter_serializes_cache_duplicate_check_and_insert():
    ctx = FakeContext()

    class StorageBackedQuery:
        def common_query(self, **kwargs):
            return list(ctx.mongo_db_cache.inserted)

    ctx.cache_query_engine = StorageBackedQuery()
    ports = IISPipelinePorts(
        FakeRegistry(ctx), FakeClientManager(), analyzer=_analyzer,
        scorer_factory=lambda _: FakeScorer())
    event = HubEvent(INTAKE_RECEIVED, _original(), "finance")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: ports.accept(event), range(2)))

    assert sum(result.accepted for result in results) == 1
    assert len(ctx.mongo_db_cache.inserted) == 1


def test_manual_test_source_is_rejected_from_persistent_intake():
    ctx = FakeContext()
    ports = IISPipelinePorts(
        FakeRegistry(ctx), FakeClientManager(), analyzer=_analyzer,
        scorer_factory=lambda _: FakeScorer())
    original = _original()
    original["source"] = MANUAL_TEST_SOURCE

    result = ports.accept(HubEvent(INTAKE_RECEIVED, original, "finance"))

    assert not result.accepted
    assert result.reason == "manual_test_requires_debug_route"
    assert not ctx.mongo_db_cache.inserted


def test_transient_analysis_returns_valuable_result_without_database_writes():
    ctx = FakeContext()
    ports = IISPipelinePorts(
        FakeRegistry(ctx), FakeClientManager(), analyzer=_analyzer,
        scorer_factory=lambda _: FakeScorer(), retry_wait=wait_none())
    original = _original()
    original["source"] = MANUAL_TEST_SOURCE

    result = ports.analyze_transient(
        HubEvent("debug.analysis.requested", original, "finance"))

    assert result.accepted
    assert result.payload["SUBMITTER"] == "Manual transient debug"
    assert result.payload["APPENDIX"]["__TOTAL_SCORE__"] == 7.5
    assert not ctx.mongo_db_cache.inserted
    assert not ctx.mongo_db_cache.updated
    assert not ctx.mongo_db_archive.inserted
    assert not ctx.mongo_db_low_value.inserted
    assert ctx.stats == {"archived": 0, "dropped": 0, "error": 0}


def test_transient_low_value_result_is_displayed_without_persistence():
    ctx = FakeContext()
    ports = IISPipelinePorts(
        FakeRegistry(ctx), FakeClientManager(),
        analyzer=lambda *_: {"TAXONOMY": "无情报价值", "REASON": "测试内容"},
        scorer_factory=lambda _: FakeScorer(), retry_wait=wait_none())
    original = _original()
    original["source"] = MANUAL_TEST_SOURCE

    result = ports.analyze_transient(
        HubEvent("debug.analysis.requested", original, "finance"))

    assert result.accepted
    assert result.metadata["low_value"] is True
    assert result.payload["TAXONOMY"] == "无情报价值"
    assert not ctx.mongo_db_low_value.inserted
    assert not ctx.mongo_db_cache.updated


def test_transient_analysis_uses_dedicated_non_recording_analyzer():
    ctx = FakeContext()

    def persistent_analyzer(*_):
        raise AssertionError("persistent analyzer must not be used")

    ports = IISPipelinePorts(
        FakeRegistry(ctx), FakeClientManager(),
        analyzer=persistent_analyzer,
        transient_analyzer=_analyzer,
        scorer_factory=lambda _: FakeScorer(), retry_wait=wait_none())
    original = _original()
    original["source"] = MANUAL_TEST_SOURCE

    result = ports.analyze_transient(
        HubEvent("debug.analysis.requested", original, "finance"))

    assert result.accepted
    assert result.payload["EVENT_TITLE"] == "Market event"
