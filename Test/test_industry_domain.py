from types import SimpleNamespace
import hashlib

import pytest
from pydantic import ValidationError
from event_engine.domains import registry_for

from IntelligenceDomains.industry import create_domain
from IntelligenceDomains.industry.prompt import (
    INDUSTRY_BASELINE_PROMPT_VERSION,
    INDUSTRY_DISAMBIGUATION,
    INDUSTRY_PRODUCTION_PROMPT_VERSION,
    INDUSTRY_PROMPT_VERSION,
    INDUSTRY_PROMPT_V2_VERSION,
    INDUSTRY_PROMPT_V3_VERSION,
    INDUSTRY_PROMPT_V4_VERSION,
    INDUSTRY_PROMPT_V5_VERSION,
    INDUSTRY_PROMPT_V6_VERSION,
    INDUSTRY_V6_CLOSED_SCHEMA_GUARDRAILS,
    build_industry_prompt,
    build_industry_prompt_v2,
    build_industry_prompt_v3,
    build_industry_prompt_v4,
    build_industry_prompt_v5,
    build_industry_prompt_v6,
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
        INDUSTRY_PRODUCTION_PROMPT_VERSION,
    ]
    assert domain.prompt_catalog.selectable_versions() == [
        INDUSTRY_BASELINE_PROMPT_VERSION,
        INDUSTRY_PROMPT_V2_VERSION,
        INDUSTRY_PROMPT_V3_VERSION,
        INDUSTRY_PROMPT_V4_VERSION,
        INDUSTRY_PROMPT_V5_VERSION,
        INDUSTRY_PROMPT_VERSION,
    ]
    assert domain.subsystems.batch is None
    assert domain.subsystems.shadow.enabled is False
    assert domain.event_registry.get("layoff").label == "裁员"
    assert domain.event_registry.get("reduce_output").label == "减少产量"
    assert domain.event_registry.get("reduce_capacity").label == "削减产能"
    assert domain.event_registry.get("layoff").required_roles == ("employer",)
    assert domain.event_registry.get("close_facility").required_roles == (
        "operator", "facility")
    assert "不得把减产自动写成削减产能" in INDUSTRY_DISAMBIGUATION


def test_industry_factory_reads_domain_scoped_shadow_config():
    domain = create_domain(subsystem_config={"shadow": {
        "enabled": True, "versions": [3], "sample_rate": 0.25,
        "comparison_mode": "same_model",
    }})

    assert domain.subsystems.shadow.enabled is True
    assert domain.subsystems.shadow.versions == (3,)
    assert domain.subsystems.shadow.sample_rate == 0.25


def test_industry_rejects_overlap_between_formal_and_same_model_shadow_pools():
    with pytest.raises(ValueError, match="正式与 shadow Prompt 池重叠"):
        create_domain(subsystem_config={"shadow": {
            "enabled": True, "versions": [2, 6], "sample_rate": 0.25,
            "comparison_mode": "same_model",
        }})


def test_industry_allows_explicit_replicate_aa_of_formal_prompt():
    domain = create_domain(subsystem_config={"shadow": {
        "enabled": True, "versions": [2], "sample_rate": 0.25,
        "comparison_mode": "replicate", "experiment_id": "industry-v2-aa",
    }})

    assert domain.subsystems.shadow.versions == (2,)
    assert domain.subsystems.shadow.comparison_mode == "replicate"
    assert domain.subsystems.shadow.experiment_id == "industry-v2-aa"


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

    assert registry.require("industry").prompt_catalog.sampling_versions() == [2]
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

    # v1/v2 共用的 Event Extraction 数值协议修正后重新冻结；价值校准文本仍不变。
    assert len(prompt) == 24121
    assert hashlib.sha256(prompt.encode()).hexdigest() == (
        "3d90cb1598fd8a940610cb7d0457fbd126610d02b859bc472a25ed570d000c01"
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
    assert catalog.sampling_versions() == [2]
    assert catalog.selectable_versions() == [1, 2, 3, 4, 5, 6]


def test_industry_v3_is_stricter_shorter_and_keeps_detailed_reasoning():
    baseline = build_industry_prompt()
    v2 = build_industry_prompt_v2()
    v3 = build_industry_prompt_v3()

    assert len(v3) < len(v2)
    assert "逐维从低档开始" in v3
    assert "财报是信息载体，不是自动收录或自动排除理由" in v3
    assert "信号类型；决定性新增事实；影响的经营或投资变量；最关键限制" in v3
    assert "不得为了凑满三项混入无关公司或宏观背景" in v3
    assert "decision_relevant 还须影响核心假设并有近期可验证里程碑" in v3
    assert "逐维从低档开始" not in baseline
    catalog = create_domain().prompt_catalog
    assert catalog.protocol_for(1) is catalog.protocol_for(2)
    assert catalog.protocol_for(2) is catalog.protocol_for(3)


def test_industry_v4_adds_output_guardrails_without_changing_v3_identity():
    v3 = build_industry_prompt_v3()
    v4 = build_industry_prompt_v4()

    assert v4 != v3
    assert "REASON 必须在 100 个字符以内（包含标点）" in v4
    assert "即使只有一个实体也写成" in v4
    assert "禁止输出空对象" in v4
    assert "输出前强制自检" not in v3
    catalog = create_domain().prompt_catalog
    assert catalog.protocol_for(3) is catalog.protocol_for(4)


def test_industry_v5_adds_first_pass_guards_without_changing_v4_judgment():
    v4 = build_industry_prompt_v4()
    v5 = build_industry_prompt_v5()

    assert v5 != v4
    assert "EVENT_TITLE 最多 24 个字符" in v5
    assert "各最多 80 个字符" in v5
    assert "epistemic 限定词必须带非空 by 实体对象数组" in v5
    assert "禁止依赖接收端截断" in v5
    assert "首次校验通过自检" not in v4
    catalog = create_domain().prompt_catalog
    assert catalog.protocol_for(4) is catalog.protocol_for(5)
    assert catalog.sampling_versions() == [2]


def test_industry_v6_restarts_from_v4_with_only_closed_schema_guards():
    v4 = build_industry_prompt_v4()
    v5 = build_industry_prompt_v5()
    v6 = build_industry_prompt_v6()

    expected = v4.replace(
        "# 有价值输出结构\n",
        f"{INDUSTRY_V6_CLOSED_SCHEMA_GUARDRAILS}\n# 有价值输出结构\n",
        1,
    )
    assert v6 == expected
    assert v6 != v5
    assert "EVENT_TITLE 最多 24 个字符" not in v6
    assert "SUB_CATEGORY_NOTE" in v6
    assert "“用途”“原因”“备注”“描述”等文本不得自造为属性键" in v6
    assert "duration.type 只能是 duration" in v6
    assert "禁止输出 market" in v6
    catalog = create_domain().prompt_catalog
    assert catalog.protocol_for(4) is catalog.protocol_for(6)
    assert catalog.sampling_versions() == [2]
    assert catalog.selectable_versions() == [1, 2, 3, 4, 5, 6]


def test_industry_prompt_distinguishes_share_from_growth_and_multiplier():
    prompt = build_industry_prompt_v2()
    assert "增长率、降幅和倍数绝不能写入 ratio" in prompt
    assert '"value":783,"unit":"%"' in prompt
    assert '"value":2.5,"unit":"倍"' in prompt


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
