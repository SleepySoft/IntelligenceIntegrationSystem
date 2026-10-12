"""Prompt 协议族机制（PromptCatalog/PromptProtocol）与 pipeline 接入的测试。"""

from tenacity import wait_none

from ServiceComponent.IntelligenceHubDefines_v4 import validate_analysis_result_v4
from ServiceComponent.prompt_protocols import (
    PromptCatalog,
    PromptCatalogError,
    PromptProtocol,
)
from ServiceComponent.adapters import EventV4PipelinePorts
from ServiceComponent.runtime import HubEvent

from test_event_v4_pipeline_adapter import (
    FakeClientManager,
    FakeContext,
    FakeArchiveRepository,
    FakeRegistry,
    FakeScorer,
    _original,
    _valuable,
)


def _catalog_with(protocol_name="toy", version=77, sampling=True, prepare=None):
    catalog = PromptCatalog()
    catalog.register_protocol(PromptProtocol(
        name=protocol_name,
        validate_result=validate_analysis_result_v4,
        prepare_prompt=prepare or (lambda prompt, data: prompt),
    ))
    catalog.register_prompt(version, f"toy prompt v{version} {{{{CONTENT}}}}",
                            protocol=protocol_name, sampling=sampling)
    return catalog


def test_catalog_enforces_registration_rules():
    catalog = _catalog_with()
    try:
        catalog.register_prompt(77, "dup", protocol="toy")
        raise AssertionError("duplicate version should be rejected")
    except PromptCatalogError:
        pass
    try:
        catalog.register_prompt(78, "text", protocol="missing")
        raise AssertionError("unregistered protocol should be rejected")
    except PromptCatalogError:
        pass
    try:
        catalog.register_prompt(79, "   ", protocol="toy")
        raise AssertionError("empty prompt should be rejected")
    except PromptCatalogError:
        pass
    try:
        catalog.spec_for(999)
        raise AssertionError("unknown version should be rejected")
    except PromptCatalogError as exc:
        assert "999" in str(exc)


def test_catalog_sampling_and_selectable_views():
    catalog = _catalog_with(version=77, sampling=True)
    catalog.register_prompt(78, "rollback", protocol="toy", sampling=False)
    assert catalog.sampling_versions() == [77]
    assert catalog.selectable_versions() == [77, 78]
    assert list(catalog.sampling_table()) == [77]
    assert list(catalog.extra_table()) == [78]
    assert catalog.modes_table() == {77: "toy", 78: "toy"}
    assert catalog.protocol_for(78).name == "toy"


def test_pipeline_uses_custom_catalog_and_prepare_hook():
    seen_prompts = []

    def analyzer(client, prompt, original):
        seen_prompts.append(prompt)
        return _valuable()

    catalog = _catalog_with(
        prepare=lambda prompt, data: prompt + f"\n锚点:{data.get('pub_time', '无')}",
    )
    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        prompt_catalog=catalog,
        analyzer=analyzer,
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    result = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert result.accepted
    assert result.payload.archive.APPENDIX["__PROMPT_VERSION__"] == 77
    assert seen_prompts and "锚点:" in seen_prompts[0]
    assert list(ports._prompt_table) == [77]


def test_pipeline_rejects_shadow_version_missing_from_catalog():
    try:
        EventV4PipelinePorts(
            FakeRegistry(FakeContext()), FakeClientManager(),
            archive_repository=FakeArchiveRepository(),
            prompt_catalog=_catalog_with(),
            shadow_versions=[42],
            shadow_recorder=object(),
            analyzer=lambda *_: _valuable(),
            scorer_factory=lambda _: FakeScorer(),
            retry_wait=wait_none(),
        )
        raise AssertionError("unknown shadow version should be rejected")
    except ValueError as exc:
        assert "42" in str(exc)


def test_legacy_table_params_still_build_catalog():
    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        prompt_table={41: "legacy full prompt"},
        extra_prompt_versions={42: "legacy subset prompt"},
        extraction_modes={41: "full", 42: "core_subset"},
        analyzer=lambda *_: _valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )
    assert ports._prompt_catalog.sampling_versions() == [41]
    assert ports._prompt_catalog.selectable_versions() == [41, 42]
    assert ports._prompt_catalog.protocol_for(42).name == "core_subset"
    result = ports.analyze(HubEvent("analysis.requested", _original(), "news"))
    assert result.accepted
    assert result.payload.archive.APPENDIX["__PROMPT_VERSION__"] == 41
