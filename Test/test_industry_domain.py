from types import SimpleNamespace
import hashlib

import pytest
from pydantic import ValidationError
from event_engine.domains import registry_for

from IntelligenceDomains.industry import create_domain
from IntelligenceDomains.industry.prompt import (
    INDUSTRY_BASELINE_PROMPT_VERSION,
    INDUSTRY_DISAMBIGUATION,
    INDUSTRY_PROMPT_VERSION,
    build_industry_prompt,
    build_industry_prompt_v2,
    validate_industry_core_result,
)
from IntelligenceDomains.industry.schema import RATE_DIMENSIONS
from IntelligenceDomains.industry.scoring import IndustryScoringEngine
from IntelligenceDomains.industry.storage import (
    IndustryArchivedDataV4,
    IndustryPreparedArchive,
)
from IntelligenceDomains.news.schema import DEFAULT_EVENT_REGISTRY as NEWS_EVENT_REGISTRY
from IntelligenceDomains.registry import build_domain_registry
from ServiceComponent.analysis_messages import build_analyze_message


def _rate_levels(**overrides):
    result = {
        "行动实质性": "material",
        "规模与力度": "substantial",
        "事实确定性": "official",
        "认知增量": "meaningful",
        "预期差与异常性": "surprising",
        "经营与战略影响": "material",
        "投资可用性": "thesis_input",
        "关注度与故事性": "compelling",
    }
    result.update(overrides)
    return result


def _valuable_result(**overrides):
    result = {
        "TAXONOMY": "行业情报",
        "SUB_CATEGORY": ["组织与人事"],
        "REASON": "公司正式宣布裁员并调整组织。",
        "EVENT_TITLE": "测试公司宣布裁员500人",
        "EVENT_BRIEF": "测试公司正式宣布将裁员500人并调整相关业务。",
        "EVENT_TEXT": "测试公司正式宣布将裁员500人，并同步调整相关业务部门。",
        "IMPACT": "可能降低成本并反映业务方向调整。",
        "RATE": _rate_levels(),
        "TIPS": "关注执行范围、时间和重组费用。",
        "EVENT_EXTRACTION": {
            "events": [{
                "is_primary": True,
                "predicate": {"id": "layoff", "surface": "裁员"},
                "roles": {
                    "employer": [{"name": "测试公司", "type": "organization"}],
                    "workforce": [{"name": "500名员工", "type": "other_object"}],
                },
                "attributes": {
                    "quantity": {
                        "type": "number", "value": 500, "unit": "人", "surface": "500人",
                    },
                },
                "qualifiers": [{"type": "phase", "value": "planned", "surface": "将裁员"}],
                "relations": [],
            }],
        },
    }
    result.update(overrides)
    return result


def test_industry_factory_registers_own_prompt_schema_scorer_and_no_news_subsystems():
    domain = create_domain()

    assert domain.name == "industry"
    assert domain.prompt_catalog.sampling_versions() == [
        INDUSTRY_BASELINE_PROMPT_VERSION,
        INDUSTRY_PROMPT_VERSION,
    ]
    assert domain.subsystems.batch is None
    assert domain.subsystems.shadow is None
    assert domain.event_registry.get("layoff").label == "裁员"
    assert domain.event_registry.get("reduce_output").label == "减少产量"
    assert domain.event_registry.get("reduce_capacity").label == "削减产能"
    assert domain.event_registry.get("layoff").required_roles == ("employer",)
    assert domain.event_registry.get("close_facility").required_roles == (
        "operator", "facility")
    assert "不得把减产自动写成削减产能" in INDUSTRY_DISAMBIGUATION


def test_industry_predicate_growth_does_not_change_news_v48_registry():
    for predicate in (
        "layoff", "reduce_output", "reduce_capacity", "close_facility",
        "commercialize", "divest", "merge", "shift_business_focus",
    ):
        assert predicate not in NEWS_EVENT_REGISTRY

    shared_registry = registry_for("news", "industry", "financial")
    assert "layoff" not in shared_registry
    assert "divest" not in shared_registry


def test_builtin_industry_factory_is_resolved_from_domain_config_name():
    contexts = [SimpleNamespace(name="news", domain_factory="news"),
                SimpleNamespace(name="industry", domain_factory="industry")]

    registry = build_domain_registry(contexts, default_name="news")

    assert registry.require("industry").prompt_catalog.sampling_versions() == [1, 2]
    assert registry.require("industry").storage.__class__.__name__ == "IndustryStorage"


def test_industry_protocol_maps_levels_and_completes_event_extraction():
    result = validate_industry_core_result(_valuable_result())

    assert set(result.RATE) == RATE_DIMENSIONS
    assert result.RATE["行动实质性"] == 8
    assert result.RATE["关注度与故事性"] == 6
    assert result.EVENT_EXTRACTION.primary_event_id == "E1"
    assert result.EVENT_EXTRACTION.events[0].core.predicate.id == "layoff"
    assert result.EVENT_EXTRACTION.events[0].core.attributes.quantity.value == 500


def test_industry_predicate_required_roles_are_enforced():
    raw = _valuable_result()
    raw["EVENT_EXTRACTION"]["events"][0]["roles"] = {
        "workforce": [{"name": "500名员工", "type": "other_object"}],
    }

    with pytest.raises(ValueError, match="missing required roles.*employer"):
        validate_industry_core_result(raw)


def test_industry_schema_rejects_news_rate_dimensions_and_unknown_subcategory():
    bad_rate = _valuable_result(RATE={"影响广度": "sectoral"})
    with pytest.raises(ValueError, match="eight industry dimensions"):
        validate_industry_core_result(bad_rate)

    bad_category = _valuable_result(SUB_CATEGORY=["商业与市场"])
    with pytest.raises(ValidationError, match="unsupported industry SUB_CATEGORY"):
        validate_industry_core_result(bad_category)


def test_industry_non_intelligence_requires_structured_reason():
    with pytest.raises(ValidationError, match="NON_INTELLIGENCE_REASON"):
        validate_industry_core_result({
            "TAXONOMY": "NonIntelligence",
            "SUB_CATEGORY": [],
            "REASON": "没有具体公司行动。",
            "RATE": {},
        })

    result = validate_industry_core_result({
        "TAXONOMY": "NonIntelligence",
        "SUB_CATEGORY": [],
        "NON_INTELLIGENCE_REASON": "no_company_action",
        "REASON": "只有行业评论，没有具体公司行动。",
        "RATE": {},
    })
    assert result.is_non_intelligence


def test_industry_scoring_keeps_investment_value_and_story_interest_separate():
    engine = IndustryScoringEngine()
    baseline = validate_industry_core_result(_valuable_result())
    breakdown = engine.calculate_breakdown(baseline)

    assert breakdown.company_importance_bonus == 0
    assert breakdown.arachne_network_bonus == 0
    assert breakdown.total_score >= breakdown.value_score

    gossip = baseline.model_copy(deep=True)
    gossip.RATE.update({
        "行动实质性": 3,
        "规模与力度": 2,
        "事实确定性": 4,
        "认知增量": 4,
        "预期差与异常性": 8,
        "经营与战略影响": 2,
        "投资可用性": 2,
        "关注度与故事性": 10,
    })
    gossip_score = engine.calculate_breakdown(gossip)

    assert gossip_score.interest_score > gossip_score.value_score
    assert gossip_score.total_score > gossip_score.value_score
    assert gossip_score.total_score <= 6.5


def test_industry_storage_uses_independent_archive_schema():
    domain = create_domain()
    analysis = validate_industry_core_result(_valuable_result())
    prepared = domain.storage.prepare(
        SimpleNamespace(name="industry"),
        {"title": "原文标题", "content": "原文正文"},
        analysis,
        prompt_version=1,
        ai_service="test",
        ai_model="test-model",
        intelligence_uuid="12345678-1234-5678-1234-567812345678",
        informant="https://example.test/industry-1",
        total_score=7.2,
        quality=None,
    )

    assert not prepared.low_value
    assert isinstance(prepared.archive_payload, IndustryPreparedArchive)
    assert isinstance(prepared.archive_payload.archive, IndustryArchivedDataV4)
    assert set(prepared.archive_payload.archive.RATE) == RATE_DIMENSIONS
    assert prepared.output["ORGANIZATION"] == ["测试公司"]


def test_industry_prompt_forbids_company_and_supply_chain_guessing():
    prompt = create_domain().prompt_catalog.text_for(INDUSTRY_PROMPT_VERSION)

    assert "不得根据公司名称或知名度推断公司重要性" in prompt
    assert "不得推断正文没有说明的产业链、供应链依赖" in prompt
    assert "shift_business_focus" in prompt
    assert "close_facility" in prompt


def test_industry_v1_baseline_text_is_frozen_for_attributable_gray_test():
    prompt = build_industry_prompt()

    assert len(prompt) == 23903
    assert hashlib.sha256(prompt.encode()).hexdigest() == (
        "a85f028de8c0c6adfbe51b3733a55a71546d03f42db00529a00dbea021b73e8f"
    )


def test_industry_v2_adds_cross_type_evidence_calibration_only_in_gray_prompt():
    baseline = build_industry_prompt()
    gray = build_industry_prompt_v2()

    assert baseline != gray
    assert "公司行动、经营状态冲击、经营相关冲突还是产业政策" in gray
    assert "它可以因故事性进入情报" in gray
    assert "正文未提供基线时用 unknown，不得擅自判为 expected" in gray
    assert "不得把“有趣”或“有代表性”写成投资价值" in gray
    assert "单一品牌遭冒名招募、删评索费并报警" in gray
    assert "财报揭示利润历史低点" in gray
    assert "公司行动、经营状态冲击、经营相关冲突还是产业政策" not in baseline

    catalog = create_domain().prompt_catalog
    assert catalog.protocol_for(1) is catalog.protocol_for(2)
    assert catalog.sampling_versions() == [1, 2]


def test_industry_prompt_keeps_system_prefix_static_and_dynamic_data_in_user():
    prompt = create_domain().prompt_catalog.text_for(INDUSTRY_PROMPT_VERSION)
    first = {
        "UUID": "industry-cache-1",
        "title": "第一篇行业情报",
        "content": "正文甲",
        "informant": "test://industry/cache/1",
    }
    second = {
        "UUID": "industry-cache-2",
        "title": "第二篇行业情报",
        "content": "正文乙",
        "informant": "test://industry/cache/2",
    }

    assert "{{CURRENT_DATE}}" not in prompt
    assert "{{CONTENT}}" not in prompt

    messages_a = build_analyze_message(prompt, first)
    messages_b = build_analyze_message(prompt, second)

    assert [item["role"] for item in messages_a] == ["system", "user"]
    assert messages_a[0] == messages_b[0]
    assert "industry-cache-1" not in messages_a[0]["content"]
    assert "正文甲" not in messages_a[0]["content"]
    assert "## 当前参考时间" in messages_a[1]["content"]
    assert "industry-cache-1" in messages_a[1]["content"]
    assert "正文甲" in messages_a[1]["content"]
    assert messages_a[1] != messages_b[1]
