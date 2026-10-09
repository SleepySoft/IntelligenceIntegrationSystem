from tenacity import wait_none

from ServiceComponent.adapters import EventV4PipelinePorts
from ServiceComponent.IntelligenceHubDefines_v4 import validate_analysis_result_v4
from ServiceComponent.IntelligenceScoringEngine import IntelligenceScoringEngine
from ServiceComponent.manual_debug_analysis import MANUAL_TEST_SOURCE
from ServiceComponent.pipeline import ARCHIVE_REQUESTED
from ServiceComponent.runtime import HubEvent


class FakeStorage:
    def __init__(self):
        self.inserted = []
        self.updated = []

    def insert(self, value):
        self.inserted.append(value)

    def update(self, query, update):
        self.updated.append((query, update))


class FakeContext:
    name = "news"
    prompt_table = {40: "v4 prompt"}
    scoring_config = None
    ai_client_group = None

    def __init__(self):
        self.mongo_db_cache = FakeStorage()
        self.cache_query_engine = None
        self.stats = {"archived": 0, "dropped": 0, "error": 0}


class FakeRegistry:
    def __init__(self, context):
        self.context = context

    def resolve(self, name):
        return self.context if name == "news" else None

    def refresh_prompts(self, context):
        return context.prompt_table


class FakeClient:
    def get_api_base_url(self):
        return "fake://ai"

    def get_current_model(self):
        return "fake-v4"


class FakeClientManager:
    def __init__(self):
        self.client = FakeClient()
        self.released = []

    def get_available_client(self, user, **kwargs):
        return self.client

    def release_client(self, client):
        self.released.append(client)


class FakeArchiveRepository:
    def __init__(self):
        self.archives = []
        self.events = []
        self.low_values = []

    def contains(self, intelligence_uuid, informant):
        return False

    def save_low_value(self, record):
        self.low_values.append(record)

    def commit(self, record, events):
        self.archives.append(record)
        self.events.extend(events)


class FakeScorer:
    def calculate_v4(self, analysis):
        return 6.5


def _original():
    return {
        "UUID": "30eb0459-998d-486f-8eac-470b9dfd98ec",
        "token": "collector",
        "title": "A军袭击B设施",
        "content": "A军对B设施发动袭击。",
        "informant": "https://example.test/v4",
    }


def _valuable():
    return {
        "kind": "valuable",
        "message": {"title": "A军袭击B设施", "brief": "A军袭击B设施。", "text": "A军对B设施发动袭击。"},
        "classification": {"taxonomy": "政治与安全", "subcategories": ["国防军事"]},
        "assessment": {
            "impact": "造成安全影响。", "reason": "存在明确军事行动。", "tips": "",
            "rate": {"影响广度": 4, "影响深度": 5, "新颖性与异常性": 6, "演化与连锁潜力": 5, "舆情及认知影响": 3, "可行动性": 6},
        },
        "event_extraction": {
            "schema_version": "event-extraction/1.0", "primary_event_id": "E1",
            "entities": [
                {"id": "ENT1", "name": "A军", "type": "organization"},
                {"id": "ENT2", "name": "B设施", "type": "facility"},
            ],
            "events": [{
                "id": "E1",
                "core": {
                    "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
                    "predicate": {"id": "attack", "surface": "袭击"},
                    "roles": {"actor": ["ENT1"], "target": ["ENT2"]},
                },
            }],
        },
    }


def test_v4_adapter_retries_validation_with_compact_feedback_then_archives():
    prompts = []

    def analyzer(client, prompt, original):
        prompts.append(prompt)
        return {"kind": "valuable"} if len(prompts) == 1 else _valuable()

    context = FakeContext()
    repository = FakeArchiveRepository()
    manager = FakeClientManager()
    ports = EventV4PipelinePorts(
        FakeRegistry(context), manager,
        archive_repository=repository,
        analyzer=analyzer,
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )
    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert analyzed.accepted
    assert len(prompts) == 2
    assert "IIS 情报分析要求" in prompts[0]
    assert "上一次输出的修正要求" in prompts[1]
    assert len(manager.released) == 2

    archived = ports.archive(HubEvent(ARCHIVE_REQUESTED, analyzed.payload, "news"))
    assert archived.accepted
    assert len(repository.archives) == 1
    assert len(repository.events) == 1
    assert repository.archives[0].total_score == 6.5
    assert context.stats["archived"] == 1


def test_v4_adapter_resolves_repository_from_subsystem_context():
    context = FakeContext()
    repository = FakeArchiveRepository()
    context.event_v4_archive_repository = repository
    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        analyzer=lambda *_: _valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))
    archived = ports.archive(HubEvent(ARCHIVE_REQUESTED, analyzed.payload, "news"))

    assert analyzed.accepted
    assert archived.accepted
    assert len(repository.archives) == 1


def test_v4_adapter_stores_non_intelligence_envelope_without_events():
    context = FakeContext()
    repository = FakeArchiveRepository()
    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        archive_repository=repository,
        analyzer=lambda *_: {"kind": "non_intelligence", "reason": "广告内容"},
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    result = ports.analyze(HubEvent("analysis.requested", _original(), "news"))
    assert not result.accepted
    assert result.reason == "non_intelligence"
    assert len(repository.low_values) == 1
    assert not repository.events
    assert context.stats["dropped"] == 1


def test_v4_scorer_reads_nested_rate_with_chinese_aliases():
    analysis = validate_analysis_result_v4(_valuable())
    assert IntelligenceScoringEngine().calculate_v4(analysis) == 5.9


def test_v4_transient_analysis_returns_events_without_archive_writes():
    context = FakeContext()
    repository = FakeArchiveRepository()
    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        archive_repository=repository,
        analyzer=lambda *_: _valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )
    original = _original()
    original["source"] = MANUAL_TEST_SOURCE

    result = ports.analyze_transient(
        HubEvent("debug.analysis.requested", original, "news"))

    assert result.accepted
    assert result.metadata["transient"] is True
    assert result.payload["events"][0]["local_event_id"] == "E1"
    assert not repository.archives
    assert not repository.events
    assert not repository.low_values
    assert not context.mongo_db_cache.updated


def test_v4_transient_analysis_does_not_require_archive_repository():
    context = FakeContext()
    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        analyzer=lambda *_: _valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )
    original = _original()
    original["source"] = MANUAL_TEST_SOURCE

    result = ports.analyze_transient(
        HubEvent("debug.analysis.requested", original, "news"))

    assert result.accepted
    assert result.payload["events"][0]["local_event_id"] == "E1"


def test_v4_transient_non_intelligence_is_not_saved():
    context = FakeContext()
    repository = FakeArchiveRepository()
    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        archive_repository=repository,
        analyzer=lambda *_: {"kind": "non_intelligence", "reason": "测试广告"},
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )
    original = _original()
    original["source"] = MANUAL_TEST_SOURCE

    result = ports.analyze_transient(
        HubEvent("debug.analysis.requested", original, "news"))

    assert result.accepted
    assert result.metadata["low_value"] is True
    assert result.payload["analysis"]["kind"] == "non_intelligence"
    assert not repository.low_values


def test_v4_transient_analysis_uses_selected_prompt_override_with_normal_validation():
    context = FakeContext()
    repository = FakeArchiveRepository()
    prompts = []

    def analyzer(client, prompt, original):
        prompts.append(prompt)
        return _valuable()

    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        archive_repository=repository,
        analyzer=analyzer,
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )
    original = _original()
    original.update({
        "source": MANUAL_TEST_SOURCE,
        "prompt": "V4 覆盖版 {{CONTENT}} Prompt",
        "temp_data": {
            "manual_debug": {"prompt_version": 40, "prompt_overridden": True}
        },
    })

    result = ports.analyze_transient(
        HubEvent("debug.analysis.requested", original, "news"))

    assert result.accepted
    assert prompts == ["V4 覆盖版 {{CONTENT}} Prompt"]
    assert result.payload["debug_prompt"] == {"version": 40, "overridden": True}
