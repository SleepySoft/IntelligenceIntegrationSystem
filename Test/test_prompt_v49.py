"""v49 的独立构建、规则覆盖、输出兼容与缓存布局回归。"""

from copy import deepcopy
import json

import pytest
from tenacity import wait_none

import prompts_event_v4 as catalog_module
import prompts_event_v49 as v49
from event_engine.domains import registry_for
from ServiceComponent.IntelligenceHubDefines_v4 import RATE_LEVELS, TAXONOMY_SUBCATEGORIES
from ServiceComponent.adapters import EventV4PipelinePorts
from ServiceComponent.analysis_messages import build_analyze_message
from ServiceComponent.intelligence_contracts import NonIntelligenceReason
from ServiceComponent.manual_debug_analysis import MANUAL_TEST_SOURCE
from ServiceComponent.runtime import HubEvent
from test_event_v4_pipeline_adapter import (
    FakeArchiveRepository, FakeClientManager, FakeContext, FakeRegistry, FakeScorer, _original,
)


def _output_example():
    text = v49.VALUABLE_OUTPUT
    return json.JSONDecoder().raw_decode(text[text.index("{"):])[0]


def _rich_output():
    payload = _output_example()
    first = payload["EVENT_EXTRACTION"]["events"][0]
    first["time"] = {"event_time": {"normalized": "2026-10-20", "surface": "10月20日"}}
    first["context"] = {
        "event_location": [{"name": "乌克兰", "type": "location", "country_code": "UA"}],
    }
    first["attributes"] = {
        "amount": {"type": "money", "value": 8500000000, "currency": "CAD", "unit": "元", "surface": "85亿加元"},
        "quantity": {"type": "number", "value": 5000, "unit": "人", "surface": "5000人"},
        "ratio": {"type": "ratio", "value": 0.25, "surface": "25%"},
        "duration": {"type": "duration", "value": 3, "unit": "年", "surface": "三年"},
    }
    first["qualifiers"] = [
        {"type": "phase", "value": "ongoing"},
        {"type": "epistemic", "value": "doubted", "by": [{"name": "某机构", "type": "organization"}],
         "time": {"normalized": "2026-10-20", "surface": "截至10月20日"}},
        {"type": "directive", "value": "ordered", "by": [{"name": "某人", "type": "person"}]},
        {"type": "polarity", "value": "negated"},
    ]
    first["relations"] = [{"predicate": "precedes", "target": 2}]
    payload["EVENT_EXTRACTION"]["events"].extend([
        {
            "is_primary": False,
            "predicate": {"id": "move", "surface": "部署"},
            "agency": "agentive",
            "roles": {"theme": [{"name": "某武装组织", "type": "organization"}]},
            "qualifiers": [{"type": "phase", "value": "planned"}],
            "time": {"expected_start_time": {"normalized": "2026-11", "surface": "11月"}},
        },
        {
            "is_primary": False,
            "predicate": {"id": None, "surface": "特殊行动", "gloss": "未登记的行动"},
            "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
            "roles": deepcopy(first["roles"]),
        },
    ])
    return payload


def test_v49_remains_selectable_during_v52_gray_sampling():
    catalog = catalog_module.EVENT_V4_PROMPT_CATALOG
    assert catalog.sampling_versions() == [48, 52]
    assert catalog.protocol_for(49) is catalog.protocol_for(48)
    assert catalog.text_for(49) == v49.EVENT_ANALYSIS_PROMPT_V49
    assert 49 not in catalog_module.EVENT_ANALYSIS_PROMPT_TABLE
    assert catalog_module.EVENT_ANALYSIS_EXTRA_PROMPTS[49] == v49.EVENT_ANALYSIS_PROMPT_V49


def test_v49_build_does_not_consume_historical_prompts(monkeypatch):
    import event_engine.extraction as extraction

    def forbidden(*args, **kwargs):
        raise AssertionError("新版不能调用旧版 Prompt 构造器")

    for version in range(41, 49):
        monkeypatch.setattr(catalog_module, f"EVENT_ANALYSIS_PROMPT_V{version}", "历史版本已不可用")
    for name in ("build_core_extraction_section", "build_event_extraction_section", "build_predicate_catalog"):
        monkeypatch.setattr(extraction, name, forbidden)
    assert v49.build_event_v49_prompt() == v49.EVENT_ANALYSIS_PROMPT_V49


def test_selected_registry_controls_v49_catalog():
    registry = registry_for("news")
    catalog = catalog_module.build_event_v4_prompt_catalog(registry)
    text = catalog.text_for(49)
    assert "- attack |" in text
    assert "- build_facility |" not in text
    assert "- issue_bond |" not in text


def test_complete_rating_taxonomy_and_reason_rules_remain():
    sections = v49.build_event_v49_sections()
    for dimension, levels in RATE_LEVELS.items():
        line = next(line for line in sections["rating"].splitlines() if line.startswith(f"- {dimension}："))
        positions = []
        for level, _score, gloss, note in levels:
            positions.append(line.index(f"{level}（{gloss}）"))
            if note:
                assert note in line
        assert positions == sorted(positions)
    for category, subcategories in TAXONOMY_SUBCATEGORIES.items():
        line = next(line for line in sections["taxonomy"].splitlines() if line.startswith(f"- {category}："))
        assert set(line.split("：", 1)[1].removesuffix("。").split("、")) == subcategories
    assert {reason for reason, _ in v49.NON_INTELLIGENCE_DESCRIPTIONS} == (
        set(NonIntelligenceReason) - {NonIntelligenceReason.UNSPECIFIED}
    )
    assert "最高选第 2 级" in sections["rating"]
    assert "最高选第 4 级" in sections["rating"]
    assert "不构成高档证据" in sections["rating"]
    assert v49.EVENT_ANALYSIS_PROMPT_V49.count('"NON_INTELLIGENCE_REASON":') == 1


@pytest.mark.parametrize("payload", [_output_example(), _rich_output()])
def test_v48_v49_bridge_the_same_valuable_output(payload):
    catalog = catalog_module.EVENT_V4_PROMPT_CATALOG
    before = catalog.protocol_for(48).validate_result(deepcopy(payload))
    after = catalog.protocol_for(49).validate_result(deepcopy(payload))
    assert before.model_dump() == after.model_dump()
    assert after.EVENT_EXTRACTION.primary_event_id == "E1"
    assert after.RATE["可行动性"] == 4


@pytest.mark.parametrize("reason", [reason for reason, _ in v49.NON_INTELLIGENCE_DESCRIPTIONS])
def test_v48_v49_accept_the_same_low_value_reasons(reason):
    payload = {
        "TAXONOMY": "NonIntelligence", "SUB_CATEGORY": [],
        "NON_INTELLIGENCE_REASON": reason.value, "REASON": "低价值原因", "RATE": {},
    }
    catalog = catalog_module.EVENT_V4_PROMPT_CATALOG
    assert catalog.protocol_for(48).validate_result(payload) == catalog.protocol_for(49).validate_result(payload)


@pytest.mark.parametrize("version", [48, 49])
def test_missing_reason_and_numeric_rating_remain_invalid(version):
    protocol = catalog_module.EVENT_V4_PROMPT_CATALOG.protocol_for(version)
    with pytest.raises(ValueError, match="NON_INTELLIGENCE_REASON"):
        protocol.validate_result({"TAXONOMY": "NonIntelligence", "REASON": "无增量", "RATE": {}})
    payload = _output_example()
    payload["RATE"]["可行动性"] = 4
    with pytest.raises(ValueError, match="未登记等级词"):
        protocol.validate_result(payload)


def test_v49_preserves_static_prefix_dynamic_message_and_retry_suffix():
    prompt = v49.EVENT_ANALYSIS_PROMPT_V49
    first = {"UUID": "v49-1", "title": "第一篇", "content": "正文甲"}
    second = {"UUID": "v49-2", "title": "第二篇", "content": "正文乙"}
    a, b = [build_analyze_message(prompt, original) for original in (first, second)]
    assert [message["role"] for message in a] == ["system", "user"]
    assert a[0] == b[0] == {"role": "system", "content": prompt}
    assert a[1] != b[1]
    assert a[1] == build_analyze_message(catalog_module.EVENT_ANALYSIS_PROMPT_V48, first)[1]
    feedback = "\n\n# 上一次输出的修正要求\n修正时间格式"
    retry = build_analyze_message(prompt + feedback, first)
    assert retry[0]["content"] == a[0]["content"] + feedback
    assert retry[1] == a[1]
    assert len(prompt) < len(catalog_module.EVENT_ANALYSIS_PROMPT_V48) * 0.70


def test_manual_pipeline_can_select_v49_without_overrides_or_persistence():
    prompts = []

    def analyzer(client, prompt, original, record_meta=None):
        prompts.append(prompt)
        return _output_example()

    repository = FakeArchiveRepository()
    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(), archive_repository=repository,
        transient_analyzer=analyzer, scorer_factory=lambda _: FakeScorer(), retry_wait=wait_none(),
    )
    original = _original()
    original.update({
        "source": MANUAL_TEST_SOURCE,
        "temp_data": {"manual_debug": {"prompt_version": 49}},
    })
    result = ports.analyze_transient(HubEvent("debug.analysis.requested", original, "news"))
    assert result.accepted
    assert prompts == [v49.EVENT_ANALYSIS_PROMPT_V49]
    assert result.payload["debug_prompt"] == {"version": 49, "overridden": False}
    assert result.payload["RATE"]["可行动性"] == 4
    assert not repository.archives
