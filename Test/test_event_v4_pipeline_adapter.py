from tenacity import wait_none
from uuid import UUID

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

    @property
    def collection(self):
        return self

    def find_one(self, query, projection=None):
        del projection
        return next((item for item in self.inserted
                     if all(item.get(key) == value for key, value in query.items())), None)


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
    name = "fake-client"

    def get_api_base_url(self):
        return "fake://ai"

    def get_current_model(self):
        return "fake-v4"


class FakeClientManager:
    def __init__(self):
        self.client = FakeClient()
        self.released = []
        self.requests = []

    def wait_for_available_client(self, user, **kwargs):
        self.requests.append(kwargs)
        return self.client

    def release_client(self, client):
        self.released.append(client)

    def get_scheduling_capacity(self, target_group_id=None, **kwargs):
        return 1


class FakeFailureRecorder:
    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


class FakeArchiveRepository:
    def __init__(self):
        self.archives = []
        self.events = []
        self.low_values = []
        self.existing_sources = set()
        self.existing_ids = set()

    def contains_source(self, informant):
        return informant in self.existing_sources

    def contains_id(self, intelligence_uuid):
        return intelligence_uuid in self.existing_ids

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
        "TAXONOMY": "政治与安全", "SUB_CATEGORY": ["国防军事"],
        "EVENT_TITLE": "A军袭击B设施", "EVENT_BRIEF": "A军袭击B设施。", "EVENT_TEXT": "A军对B设施发动袭击。",
        "IMPACT": "造成安全影响。", "REASON": "存在明确军事行动。", "TIPS": "",
        "RATE": {"影响广度": 4, "影响深度": 5, "新颖性与异常性": 6, "演化与连锁潜力": 5, "舆情及认知影响": 3, "可行动性": 6},
        "EVENT_EXTRACTION": {
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


def _ports_for_intake(context=None):
    context = context or FakeContext()
    return EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        prompt_table={41: "v41 prompt"},
        analyzer=lambda *_, **__: _valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    ), context


def test_v4_intake_assigns_and_persists_uuid_for_invalid_input():
    ports, context = _ports_for_intake()
    submitted = _original()
    submitted["UUID"] = "not-a-uuid"

    result = ports.accept(HubEvent("intake.received", submitted, "news"))

    assert result.accepted
    assigned = result.payload["UUID"]
    assert str(UUID(assigned)) == assigned
    assert assigned != submitted["UUID"]
    assert context.mongo_db_cache.inserted[0]["UUID"] == assigned


def test_v4_intake_deduplicates_by_informant_not_uuid():
    ports, context = _ports_for_intake()
    first = ports.accept(HubEvent("intake.received", _original(), "news"))
    repeated_source = _original()
    repeated_source["UUID"] = "a98f33b1-10a5-499a-85ac-eb3c773d2198"

    second = ports.accept(HubEvent("intake.received", repeated_source, "news"))

    assert first.accepted
    assert not second.accepted
    assert second.reason == "collected_data_duplicated"
    assert len(context.mongo_db_cache.inserted) == 1


def test_v4_intake_rejects_blank_informant():
    ports, context = _ports_for_intake()
    submitted = _original()
    submitted["informant"] = "   "

    result = ports.accept(HubEvent("intake.received", submitted, "news"))

    assert not result.accepted
    assert not context.mongo_db_cache.inserted


def test_v4_adapter_retries_validation_with_compact_feedback_then_archives():
    prompts = []

    def analyzer(client, prompt, original, record_meta=None):
        prompts.append(prompt)
        return {"TAXONOMY": "政治与安全"} if len(prompts) == 1 else _valuable()

    context = FakeContext()
    repository = FakeArchiveRepository()
    manager = FakeClientManager()
    failure_recorder = FakeFailureRecorder()
    ports = EventV4PipelinePorts(
        FakeRegistry(context), manager,
        archive_repository=repository,
        prompt_table={41: "v41 prompt"},
        analyzer=analyzer,
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
        failure_recorder=failure_recorder,
    )
    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert analyzed.accepted
    assert len(prompts) == 2
    assert prompts[0] == "v41 prompt"
    assert "上一次输出的修正要求" in prompts[1]
    assert len(manager.released) == 2
    assert manager.requests[0]["estimated_input_tokens"] > 0
    assert manager.requests[0]["max_tokens"] == 8192
    assert manager.requests[1]["avoid_client_names"] == set()
    runtime_stats = ports.statistics
    timing_stats = {
        "last_call_ms": runtime_stats.pop("last_call_ms"),
        "call_ms_total": runtime_stats.pop("call_ms_total"),
    }
    assert runtime_stats == {
        "waiting_client": 0,
        "ai_running": 0,
        "validation_running": 0,
        "attempts": 2,
        "responses": 2,
        "retries": 1,
        "validated": 1,
        "failed": 0,
        "call_errors": 0,
        "api_errors": 0,
        "validation_errors": 1,
    }
    assert timing_stats["last_call_ms"] >= 0
    assert timing_stats["call_ms_total"] >= timing_stats["last_call_ms"]
    assert len(failure_recorder.records) == 1
    assert failure_recorder.records[0]["category"] == "validation"
    assert failure_recorder.records[0]["attempt"] == 1
    assert failure_recorder.records[0]["raw_response"] == {"TAXONOMY": "政治与安全"}

    archived = ports.archive(HubEvent(ARCHIVE_REQUESTED, analyzed.payload, "news"))
    assert archived.accepted
    assert len(repository.archives) == 1
    assert len(repository.events) == 1
    assert repository.archives[0].APPENDIX["__TOTAL_SCORE__"] == 6.5
    assert context.stats["archived"] == 1
    assert context.mongo_db_cache.updated[-1] == (
        {"UUID": _original()["UUID"]},
        {"APPENDIX.__ARCHIVED__": "A"},
    )


def test_v4_postprocess_normalizes_known_entity_names_before_projection():
    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        prompt_table={41: "v41 prompt"},
        analyzer=lambda *_, **__: _valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
        entity_name_normalizer=lambda name: {"A军": "甲军"}.get(name),
    )

    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert analyzed.accepted
    archive = analyzed.payload.archive
    assert [entity.name for entity in archive.EVENT_EXTRACTION.entities] == [
        "甲军", "B设施"]
    assert archive.ORGANIZATION == ["甲军"]


def test_v4_postprocess_keeps_original_name_when_normalizer_fails():
    def broken_normalizer(name):
        if name == "A军":
            raise RuntimeError("alias service unavailable")
        return None

    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        prompt_table={41: "v41 prompt"},
        analyzer=lambda *_, **__: _valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
        entity_name_normalizer=broken_normalizer,
    )

    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert analyzed.accepted
    assert [entity.name for entity in
            analyzed.payload.archive.EVENT_EXTRACTION.entities] == ["A军", "B设施"]
    assert ports.statistics["validation_errors"] == 0


def test_v4_adapter_deprioritizes_api_failed_client_on_retry():
    responses = [
        {"error": "local_rate_limit", "error_type": "recoverable"},
        _valuable(),
    ]
    manager = FakeClientManager()
    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), manager,
        archive_repository=FakeArchiveRepository(),
        prompt_table={41: "v41 prompt"},
        analyzer=lambda *_, **__: responses.pop(0),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert analyzed.accepted
    assert manager.requests[0]["avoid_client_names"] == set()
    assert manager.requests[1]["avoid_client_names"] == {"fake-client"}


def test_v4_adapter_removes_conversation_record_file_before_validation():
    response = _valuable()
    response["record_file"] = "analysis/internal-record.txt"
    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        prompt_table={41: "v41 prompt"},
        analyzer=lambda *_, **__: response,
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    result = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert result.accepted
    assert ports.statistics["attempts"] == 1
    assert ports.statistics["validation_errors"] == 0


def test_v4_adapter_resolves_repository_from_subsystem_context():
    context = FakeContext()
    repository = FakeArchiveRepository()
    context.event_v4_archive_repository = repository
    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        prompt_table={41: "v41 prompt"},
        analyzer=lambda *_, **__: _valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))
    archived = ports.archive(HubEvent(ARCHIVE_REQUESTED, analyzed.payload, "news"))

    assert analyzed.accepted
    assert archived.accepted
    assert len(repository.archives) == 1


def test_v4_analysis_deduplicates_archived_records_by_source_url():
    context = FakeContext()
    repository = FakeArchiveRepository()
    repository.existing_sources.add(_original()["informant"])
    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        archive_repository=repository,
        analyzer=lambda *_, **__: _valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    result = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert not result.accepted
    assert result.reason == "already_archived"
    assert context.mongo_db_cache.updated[-1] == (
        {"UUID": _original()["UUID"]},
        {"APPENDIX.__ARCHIVED__": "U"},
    )


def test_v4_analysis_reassigns_internal_uuid_collision_for_another_source():
    context = FakeContext()
    repository = FakeArchiveRepository()
    original_id = UUID(_original()["UUID"])
    repository.existing_ids.add(original_id)
    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        archive_repository=repository,
        prompt_table={41: "v41 prompt"},
        analyzer=lambda *_, **__: _valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    result = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert result.accepted
    assert UUID(result.payload.archive.UUID) != original_id
    assert context.mongo_db_cache.updated[-1] == (
        {"UUID": str(original_id)},
        {"UUID": result.payload.archive.UUID},
    )


def test_v4_adapter_stores_non_intelligence_envelope_without_events():
    context = FakeContext()
    repository = FakeArchiveRepository()
    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        archive_repository=repository,
        analyzer=lambda *_, **__: {"TAXONOMY": "NonIntelligence", "SUB_CATEGORY": [], "REASON": "广告内容", "RATE": {}},
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    result = ports.analyze(HubEvent("analysis.requested", _original(), "news"))
    assert not result.accepted
    assert result.reason == "non_intelligence"
    assert len(repository.low_values) == 1
    assert not repository.events
    assert context.stats["dropped"] == 1
    assert context.mongo_db_cache.updated[-1] == (
        {"UUID": _original()["UUID"]},
        {"APPENDIX.__ARCHIVED__": "D"},
    )


def test_v4_scorer_reads_nested_rate_with_chinese_aliases():
    analysis = validate_analysis_result_v4(_valuable())
    assert IntelligenceScoringEngine().calculate_v4(analysis) == 5.9


def test_v4_validation_reports_only_the_selected_result_kind():
    invalid = _valuable()
    invalid["record_file"] = "internal.txt"

    try:
        validate_analysis_result_v4(invalid)
    except Exception as exc:
        locations = [tuple(item["loc"]) for item in exc.errors(include_url=False)]
    else:
        raise AssertionError("validation should fail")

    assert ("record_file",) in locations


def test_v4_transient_analysis_returns_events_without_archive_writes():
    context = FakeContext()
    repository = FakeArchiveRepository()
    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        archive_repository=repository,
        prompt_table={41: "v41 prompt"},
        analyzer=lambda *_, **__: _valuable(),
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
        prompt_table={41: "v41 prompt"},
        analyzer=lambda *_, **__: _valuable(),
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
        analyzer=lambda *_, **__: {"TAXONOMY": "NonIntelligence", "SUB_CATEGORY": [], "REASON": "测试广告", "RATE": {}},
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )
    original = _original()
    original["source"] = MANUAL_TEST_SOURCE

    result = ports.analyze_transient(
        HubEvent("debug.analysis.requested", original, "news"))

    assert result.accepted
    assert result.metadata["low_value"] is True
    assert result.payload["TAXONOMY"] == "NonIntelligence"
    assert not repository.low_values


def test_v4_transient_analysis_uses_selected_prompt_override_with_normal_validation():
    context = FakeContext()
    repository = FakeArchiveRepository()
    prompts = []

    def analyzer(client, prompt, original, record_meta=None):
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
            "manual_debug": {"prompt_version": 41, "prompt_overridden": True}
        },
    })

    result = ports.analyze_transient(
        HubEvent("debug.analysis.requested", original, "news"))

    assert result.accepted
    assert prompts == ["V4 覆盖版 {{CONTENT}} Prompt"]
    assert result.payload["debug_prompt"] == {"version": 41, "overridden": True}


def _valuable_core_subset():
    """核心子集协议（v42）的 AI 输出：无 ID、无 frame、实体内联。"""
    result = _valuable()
    result["EVENT_EXTRACTION"] = {
        "events": [{
            "is_primary": True,
            "predicate": {"id": "attack", "surface": "袭击"},
            "roles": {
                "actor": [{"name": "A军", "type": "organization"}],
                "target": [{"name": "B设施", "type": "facility"}],
            },
        }],
    }
    return result


def test_v4_adapter_completes_core_subset_before_archiving():
    context = FakeContext()
    repository = FakeArchiveRepository()
    ports = EventV4PipelinePorts(
        FakeRegistry(context), FakeClientManager(),
        archive_repository=repository,
        prompt_table={42: "core subset prompt"},
        analyzer=lambda *_, **__: _valuable_core_subset(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert analyzed.accepted
    extraction = analyzed.payload.archive.EVENT_EXTRACTION
    assert extraction.schema_version == "event-extraction/1.0"
    assert extraction.primary_event_id == "E1"
    assert [entity.id for entity in extraction.entities] == ["ENT1", "ENT2"]
    assert extraction.events[0].core.roles == {"actor": ["ENT1"], "target": ["ENT2"]}
    archived = ports.archive(HubEvent(ARCHIVE_REQUESTED, analyzed.payload, "news"))
    assert archived.accepted
    assert repository.archives[0].APPENDIX["__PROMPT_VERSION__"] == 42
    assert len(repository.events) == 1


def test_v4_adapter_core_subset_validation_failure_retries_with_feedback():
    prompts = []

    def analyzer(client, prompt, original, record_meta=None):
        prompts.append(prompt)
        if len(prompts) == 1:
            # 已登记谓词不得携带 frame，桥接层拒绝后重试。
            bad = _valuable_core_subset()
            bad["EVENT_EXTRACTION"]["events"][0]["frame"] = {
                "dynamics": "process", "topology": "targeted", "agency": "agentive",
            }
            return bad
        return _valuable_core_subset()

    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        prompt_table={42: "core subset prompt"},
        analyzer=analyzer,
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert analyzed.accepted
    assert len(prompts) == 2
    assert "上一次输出的修正要求" in prompts[1]
    assert ports.statistics["validation_errors"] == 1


def test_v4_debug_prompt_catalog_exposes_extra_versions_without_sampling_them():
    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        analyzer=lambda *_, **__: _valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    table = ports.get_transient_prompt_table("news")

    assert set(table) == {41, 42, 43, 44}
    assert "核心子集" in table[42]
    # 生产抽样表已切换为只有 44。
    assert list(ports._prompt_table) == [44]


class FakeShadowRecorder:
    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


def _ports_with_shadow(analyzer, shadow_recorder):
    return EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        prompt_table={41: "full prompt"},
        extra_prompt_versions={42: "shadow prompt"},
        extraction_modes={41: "full", 42: "core_subset"},
        shadow_versions=[42],
        shadow_recorder=shadow_recorder,
        analyzer=analyzer,
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )


def test_v4_shadow_chain_runs_parallel_analysis_without_touching_archive():
    def analyzer(client, prompt, original, record_meta=None):
        return _valuable_core_subset() if prompt == "shadow prompt" else _valuable()

    shadow_recorder = FakeShadowRecorder()
    repository = FakeArchiveRepository()
    ports = _ports_with_shadow(analyzer, shadow_recorder)
    ports._archive_repository = repository

    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert analyzed.accepted
    # 主链仍是 v41 直出，归档内容不受影响。
    assert analyzed.payload.archive.APPENDIX["__PROMPT_VERSION__"] == 41
    extraction = analyzed.payload.archive.EVENT_EXTRACTION
    assert [entity.id for entity in extraction.entities] == ["ENT1", "ENT2"]
    # 影子链路执行了 v42 子集分析并落盘对比记录。
    assert len(shadow_recorder.records) == 1
    record = shadow_recorder.records[0]
    assert record["primary_prompt_version"] == 41
    assert record["shadow_prompt_version"] == 42
    shadow_extraction = record["analysis"]["EVENT_EXTRACTION"]
    assert shadow_extraction["schema_version"] == "event-extraction/1.0"
    assert [entity["id"] for entity in shadow_extraction["entities"]] == ["ENT1", "ENT2"]
    assert record["extraction_quality"] is not None
    assert ports.shadow_statistics == {"recorded": 1, "failed": 0}


def test_v4_shadow_failure_does_not_affect_primary_chain():
    def analyzer(client, prompt, original, record_meta=None):
        if prompt == "shadow prompt":
            raise RuntimeError("shadow ai unavailable")
        return _valuable()

    shadow_recorder = FakeShadowRecorder()
    ports = _ports_with_shadow(analyzer, shadow_recorder)

    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert analyzed.accepted
    assert analyzed.payload.archive.APPENDIX["__PROMPT_VERSION__"] == 41
    assert not shadow_recorder.records
    assert ports.shadow_statistics == {"recorded": 0, "failed": 1}


def test_v4_archive_appendix_carries_extraction_quality_stats():
    ports, _ = _ports_for_intake()
    analyzed = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert analyzed.accepted
    quality = analyzed.payload.archive.APPENDIX["__EXTRACTION_QUALITY__"]
    assert quality["unregistered_role_count"] == 0
    assert quality["role_type_conflict_count"] == 0
    # _original 正文包含“袭击”，v41 样例 surface 逐字命中。
    assert quality["surface_grounding"]["predicate"] == {"total": 1, "hit": 1}


def test_v4_shadow_versions_must_be_configured():
    try:
        EventV4PipelinePorts(
            FakeRegistry(FakeContext()), FakeClientManager(),
            archive_repository=FakeArchiveRepository(),
            shadow_versions=[99],
            shadow_recorder=FakeShadowRecorder(),
            analyzer=lambda *_, **__: _valuable(),
            scorer_factory=lambda _: FakeScorer(),
            retry_wait=wait_none(),
        )
    except ValueError as exc:
        assert "99" in str(exc)
    else:
        raise AssertionError("unknown shadow version should be rejected")
