from types import SimpleNamespace

from IntelligenceDomains import (
    DomainDefinition,
    DomainPreparedResult,
    DomainRegistry,
    DomainSubsystems,
    PreparedDomainArchive,
)
from ServiceComponent.adapters import EventV4PipelinePorts
from ServiceComponent.pipeline.contracts import StageResult
from ServiceComponent.prompt_protocols import PromptCatalog, PromptProtocol
from ServiceComponent.runtime import HubEvent


class _ResourceRegistry:
    default_name = "news"

    def __init__(self):
        self.contexts = {
            name: SimpleNamespace(
                name=name, scoring_config=None, mongo_db_cache=None,
                stats={"archived": 0, "dropped": 0, "error": 0},
            )
            for name in ("news", "industry")
        }

    def enabled_subsystems(self):
        return list(self.contexts.values())

    def resolve(self, name):
        return self.contexts.get(name)


class _Storage:
    def __init__(self, marker):
        self.marker = marker
        self.prepared = []
        self.committed = []

    def prepare(self, ctx, original_data, analysis, **kwargs):
        self.prepared.append((ctx.name, kwargs["total_score"]))
        return DomainPreparedResult(
            low_value=False,
            archive_payload={"marker": self.marker},
            output={"marker": self.marker},
        )

    def commit(self, ctx, payload):
        self.committed.append((ctx.name, payload))
        return {"domain": ctx.name, **payload}

    @staticmethod
    def serialize(record):
        return dict(record)


def _definition(name, *, score):
    catalog = PromptCatalog()
    catalog.register_protocol(PromptProtocol(
        name="test",
        validate_result=lambda raw: SimpleNamespace(
            is_non_intelligence=False,
            EVENT_EXTRACTION=None,
            marker=f"{name}:{raw['marker']}",
        ),
    ))
    catalog.register_prompt(score, f"{name} prompt", protocol="test", sampling=True)
    storage = _Storage(name)
    return DomainDefinition(
        name=name,
        prompt_catalog=catalog,
        scorer_factory=lambda _config: SimpleNamespace(
            calculate=lambda _result: score / 10),
        storage=storage,
        subsystems=DomainSubsystems(),
    )


def test_pipeline_resolves_prompt_validation_scoring_and_storage_by_domain():
    resources = _ResourceRegistry()
    domains = DomainRegistry(default_name="news")
    news = _definition("news", score=10)
    industry = _definition("industry", score=20)
    domains.register(news)
    domains.register(industry)
    ports = EventV4PipelinePorts(
        resources, SimpleNamespace(), domain_registry=domains,
        analyzer=lambda *_args, **_kwargs: {},
    )

    assert ports.get_transient_prompt_table("news") == {10: "news prompt"}
    assert ports.get_transient_prompt_table("industry") == {20: "industry prompt"}
    assert ports._validate_analysis(
        resources.resolve("industry"), {"marker": "ok"}, 20,
    ).marker == "industry:ok"

    ctx = resources.resolve("industry")
    result = ports.prepare_analysis_outcome(
        ctx, {}, SimpleNamespace(is_non_intelligence=False, EVENT_EXTRACTION=None),
        prompt_version=20, ai_service="test", ai_model="test",
        intelligence_uuid="uuid-1", informant="https://example.test",
        persist=True,
    )
    assert result.accepted
    assert industry.storage.prepared == [("industry", 2.0)]
    assert news.storage.prepared == []

    archived = ports.archive(HubEvent("archive.requested", result.payload, "industry"))
    assert archived.accepted
    assert industry.storage.committed == [
        ("industry", {"marker": "industry"}),
    ]


def test_archive_envelope_cannot_cross_domain_boundary():
    resources = _ResourceRegistry()
    domains = DomainRegistry(default_name="news")
    domains.register(_definition("news", score=10))
    domains.register(_definition("industry", score=20))
    ports = EventV4PipelinePorts(
        resources, SimpleNamespace(), domain_registry=domains,
        analyzer=lambda *_args, **_kwargs: {},
    )
    payload = PreparedDomainArchive("news", {}, "uuid-1")

    result = ports.archive(HubEvent("archive.requested", payload, "industry"))

    assert not result.accepted
    assert result.reason == "invalid_domain_archive_payload"
