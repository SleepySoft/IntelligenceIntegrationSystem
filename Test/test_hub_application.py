from ServiceComponent.HubApplication import HubApplication
from ServiceComponent.pipeline import ARCHIVE_COMPLETED, StageResult
from ServiceComponent.DomainResources import DomainContext, DomainResourceRegistry


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

    def describe_domains(self):
        return [{"name": "news"}]

    def refresh_prompts(self, ctx):
        return ctx.prompt_table


class Ports:
    def accept(self, event):
        if event.payload.get("reject"):
            return StageResult.reject("invalid")
        return StageResult.allow(event.payload)

    def analyze(self, event):
        return StageResult.allow({"analysis": event.payload})

    def archive(self, event):
        return StageResult.allow({"archive": event.payload})


def test_application_composes_runtime_without_infrastructure_construction():
    app = HubApplication(
        domain_resources=Registry(), ai_client_manager=object(), pipeline_ports=Ports())
    archived = []
    app.runtime.subscribe(ARCHIVE_COMPLETED, lambda event, _: archived.append(event.payload))

    app.startup()
    assert app.submit_collected_data({"content": "opaque", "domain": "news"})
    assert app.runtime.wait_for_idle(timeout=1)
    app.shutdown()

    assert archived == [{"archive": {"analysis": {"content": "opaque", "domain": "news"}}}]
    assert app.statistics["runtime"]["processed"] >= 3


def test_application_rejects_unknown_domain_before_queueing():
    app = HubApplication(
        domain_resources=Registry(), ai_client_manager=object(), pipeline_ports=Ports())

    assert not app.submit_collected_data({"domain": "unknown"})


def test_application_rejects_conflicting_domain_aliases_before_queueing():
    app = HubApplication(
        domain_resources=Registry(), ai_client_manager=object(), pipeline_ports=Ports())

    assert app.submit_collected_data_result({
        "domain": "news", "subsystem": "finance",
    }) == (False, "conflicting_domain")


def test_application_rejects_submission_before_startup():
    app = HubApplication(
        domain_resources=Registry(), ai_client_manager=object(), pipeline_ports=Ports())

    assert not app.submit_collected_data({"domain": "news", "content": "not started"})


def test_application_reports_intake_rejection_instead_of_queued():
    app = HubApplication(
        domain_resources=Registry(), ai_client_manager=object(), pipeline_ports=Ports())
    app.startup()

    assert not app.submit_collected_data({"domain": "news", "reject": True})

    app.shutdown()


def test_application_returns_rejection_reason():
    app = HubApplication(
        domain_resources=Registry(), ai_client_manager=object(), pipeline_ports=Ports())
    app.startup()
    try:
        accepted, reason = app.submit_collected_data_result({"domain": "news", "reject": True})
        assert not accepted
        assert reason == "invalid"

        accepted, reason = app.submit_collected_data_result({"domain": "news", "content": "ok"})
        assert accepted
        assert reason == ""
    finally:
        app.shutdown()

    # 未启动/未知领域的拒绝原因
    app2 = HubApplication(
        domain_resources=Registry(), ai_client_manager=object(), pipeline_ports=Ports())
    assert app2.submit_collected_data_result({"domain": "unknown"}) == (False, "unknown_domain")
    assert app2.submit_collected_data_result({"domain": "news"})[1] == "hub_not_started"


def test_application_releases_backpressure_slot_after_terminal_event():
    app = HubApplication(
        domain_resources=Registry(), ai_client_manager=object(), pipeline_ports=Ports(),
        max_inflight=1,
    )
    app.startup()

    assert app.submit_collected_data({"content": "first", "domain": "news"})
    assert app.runtime.wait_for_idle(timeout=1)
    assert app.submit_collected_data({"content": "second", "domain": "news"})
    assert app.runtime.wait_for_idle(timeout=1)
    assert app.statistics["runtime"]["in_flight_submissions"] == 0

    app.shutdown()


def test_application_requires_pipeline_ports_from_composition_root():
    try:
        HubApplication(domain_resources=Registry(), ai_client_manager=object())
    except ValueError as exc:
        assert "pipeline_ports" in str(exc)
    else:
        raise AssertionError("应用门面不得隐式选择 IIS 领域端口")


def test_application_accepts_optional_search_and_named_services_from_composition_root():
    sentinel = object()
    app = HubApplication(
        domain_resources=Registry(), ai_client_manager=object(), pipeline_ports=Ports(),
        vector_search=lambda **kwargs: [("vector-id", 0.9, kwargs)],
        services={"vector": sentinel},
    )

    assert app.vector_search_intelligence(text="query") == [("vector-id", 0.9, {"text": "query"})]
    assert app.get_service("vector") is sentinel
    assert app.get_service("missing", "fallback") == "fallback"


def test_registry_description_includes_current_process_outcomes():
    registry = DomainResourceRegistry()
    context = DomainContext(name="news", display_name="国际新闻")
    context.stats.update({"archived": 7, "dropped": 3, "error": 2})
    registry.add(context)

    assert registry.describe_domains()[0]["stats"] == {
        "archived": 7, "dropped": 3, "error": 2}


def test_application_reports_persistent_workflow_outcomes():
    class CountCollection:
        def __init__(self, count, states=None):
            self.count = count
            self.states = states or []

        def count_documents(self, _query):
            return self.count

        def aggregate(self, _pipeline):
            return list(self.states)

    class PersistentContext(Context):
        display_name = "国际新闻"
        mongo_db_cache = type("Cache", (), {"collection": CountCollection(15, [
            {"_id": "A", "count": 8},
            {"_id": "D", "count": 3},
            {"_id": "E", "count": 2},
            {"_id": "S", "count": 1},
            {"_id": "__PENDING__", "count": 1},
        ])})()
        event_v4_intelligence_collection = CountCollection(8)
        event_v4_low_value_collection = CountCollection(3)

    class PersistentRegistry(Registry):
        def __init__(self):
            self.ctx = PersistentContext()

        def enabled_domains(self):
            return [self.ctx]

    app = HubApplication(
        domain_resources=PersistentRegistry(), ai_client_manager=object(),
        pipeline_ports=Ports())

    outcome = app.statistics["workflow_outcomes"][0]
    assert outcome == {
        "name": "news",
        "display_name": "国际新闻",
        "received": 15,
        "pending": 1,
        "archived": 8,
        "low_value": 3,
        "failed": 2,
        "sensitive": 1,
        "duplicated": 0,
        "retry": 0,
        "retry_r1": 0,
        "retry_r2": 0,
        "retry_r3": 0,
        "deprecated": 0,
    }
