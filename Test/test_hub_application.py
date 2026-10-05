from ServiceComponent.HubApplication import HubApplication
from ServiceComponent.pipeline import ARCHIVE_COMPLETED, StageResult


class Context:
    name = "news"
    prompt_table = {1: "prompt"}
    cache_query_engine = None
    archive_query_engine = None
    statistics_engine = object()
    mongo_db_archive = None


class Registry:
    def __init__(self):
        self.ctx = Context()

    def default(self):
        return self.ctx

    def resolve(self, name):
        return self.ctx if name in (None, "", "news") else None

    def describe(self):
        return [{"name": "news"}]

    def refresh_prompts(self, ctx):
        return ctx.prompt_table


class Ports:
    def accept(self, event):
        return StageResult.allow(event.payload)

    def analyze(self, event):
        return StageResult.allow({"analysis": event.payload})

    def archive(self, event):
        return StageResult.allow({"archive": event.payload})


def test_application_composes_runtime_without_infrastructure_construction():
    app = HubApplication(
        subsystem_registry=Registry(), ai_client_manager=object(), pipeline_ports=Ports())
    archived = []
    app.runtime.subscribe(ARCHIVE_COMPLETED, lambda event, _: archived.append(event.payload))

    app.startup()
    assert app.submit_collected_data({"content": "opaque", "subsystem": "news"})
    assert app.runtime.wait_for_idle(timeout=1)
    app.shutdown()

    assert archived == [{"archive": {"analysis": {"content": "opaque", "subsystem": "news"}}}]
    assert app.statistics["runtime"]["processed"] >= 3


def test_application_rejects_unknown_subsystem_before_queueing():
    app = HubApplication(
        subsystem_registry=Registry(), ai_client_manager=object(), pipeline_ports=Ports())

    assert not app.submit_collected_data({"subsystem": "unknown"})


def test_application_requires_pipeline_ports_from_composition_root():
    try:
        HubApplication(subsystem_registry=Registry(), ai_client_manager=object())
    except ValueError as exc:
        assert "pipeline_ports" in str(exc)
    else:
        raise AssertionError("应用门面不得隐式选择 IIS 领域端口")


def test_application_accepts_optional_search_and_named_services_from_composition_root():
    sentinel = object()
    app = HubApplication(
        subsystem_registry=Registry(), ai_client_manager=object(), pipeline_ports=Ports(),
        vector_search=lambda **kwargs: [("vector-id", 0.9, kwargs)],
        services={"vector": sentinel},
    )

    assert app.vector_search_intelligence(text="query") == [("vector-id", 0.9, {"text": "query"})]
    assert app.get_service("vector") is sentinel
    assert app.get_service("missing", "fallback") == "fallback"
