from ServiceComponent.adapters import IISPipelinePorts
from ServiceComponent.pipeline import (
    ARCHIVE_COMPLETED,
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

    def __init__(self):
        self.mongo_db_cache = FakeStorage()
        self.mongo_db_archive = FakeStorage()
        self.mongo_db_low_value = FakeStorage()
        self.cache_query_engine = FakeQuery()
        self.archive_query_engine = FakeQuery()


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
