"""v43 RATE 等级词映射与协议链路测试。"""

import pytest
from tenacity import wait_none

from ServiceComponent.IntelligenceHubDefines_v4 import RATE_DIMENSIONS, map_rate_levels
from ServiceComponent.adapters import EventV4PipelinePorts
from ServiceComponent.runtime import HubEvent

from test_event_v4_pipeline_adapter import (
    FakeClientManager,
    FakeContext,
    FakeArchiveRepository,
    FakeRegistry,
    FakeScorer,
    _original,
)


def _levels_payload(**overrides):
    rate = {
        "影响广度": "local",
        "影响深度": "material",
        "新颖性与异常性": "uncommon",
        "演化与连锁潜力": "contained",
        "舆情及认知影响": "visible",
        "可行动性": "monitorable",
    }
    rate.update(overrides)
    return rate


def test_map_rate_levels_deterministic():
    mapped = map_rate_levels(_levels_payload())
    assert mapped == {
        "影响广度": 4, "影响深度": 6, "新颖性与异常性": 4,
        "演化与连锁潜力": 4, "舆情及认知影响": 5, "可行动性": 4,
    }
    assert set(mapped) == RATE_DIMENSIONS


def test_map_rate_levels_unknown_is_one():
    mapped = map_rate_levels(_levels_payload(**{d: "unknown" for d in RATE_DIMENSIONS}))
    assert all(score == 1 for score in mapped.values())


def test_map_rate_levels_rejects_unknown_level_and_dimension_set():
    with pytest.raises(ValueError, match="未登记等级词"):
        map_rate_levels(_levels_payload(影响广度="huge"))
    with pytest.raises(ValueError, match="six"):
        map_rate_levels({"影响广度": "local"})


def _v43_valuable():
    return {
        "TAXONOMY": "政治与安全", "SUB_CATEGORY": ["国防军事"],
        "EVENT_TITLE": "A军袭击B设施", "EVENT_BRIEF": "A军袭击B设施。",
        "EVENT_TEXT": "A军对B设施发动袭击。", "IMPACT": "造成安全影响。",
        "REASON": "存在明确军事行动。", "TIPS": "",
        "RATE": _levels_payload(),
        "EVENT_EXTRACTION": {
            "events": [{
                "is_primary": True,
                "predicate": {"id": "attack", "surface": "袭击"},
                "roles": {
                    "actor": [{"name": "A军", "type": "organization"}],
                    "target": [{"name": "B设施", "type": "facility"}],
                },
            }],
        },
    }


def test_v43_production_maps_levels_to_scores_end_to_end():
    # 默认目录的生产版本（当前 v43+ 词表协议）：等级词 RATE + 核心子集抽取。
    from prompts_event_v4 import EVENT_V4_PROMPT_CATALOG
    production_version = EVENT_V4_PROMPT_CATALOG.sampling_versions()[0]
    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        analyzer=lambda *_, **__: _v43_valuable(),
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    result = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert result.accepted
    archive = result.payload.archive
    assert archive.APPENDIX["__PROMPT_VERSION__"] == production_version
    # RATE 已被映射为整数，走原有评分器无差异。
    assert archive.RATE["影响广度"] == 4
    assert archive.RATE["影响深度"] == 6
    assert all(isinstance(v, int) for v in archive.RATE.values())
    # 事件子集仍经桥接补齐。
    assert archive.EVENT_EXTRACTION.schema_version == "event-extraction/1.0"


def test_v43_rejects_numeric_rate_with_retry():
    prompts = []

    def analyzer(client, prompt, original, record_meta=None):
        prompts.append(prompt)
        payload = _v43_valuable()
        if len(prompts) == 1:
            payload["RATE"] = _levels_payload()
            payload["RATE"]["影响广度"] = 7  # 数字分数在 v43 下非法
        return payload

    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        analyzer=analyzer,
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
    )

    result = ports.analyze(HubEvent("analysis.requested", _original(), "news"))

    assert result.accepted
    assert len(prompts) == 2
    assert "上一次输出的修正要求" in prompts[1]
    assert result.payload.archive.RATE["影响广度"] == 4


def test_v46_requires_structured_reason_but_legacy_protocol_remains_compatible():
    from prompts_event_v4 import EVENT_V4_PROMPT_CATALOG

    legacy_payload = {
        "TAXONOMY": "NonIntelligence", "SUB_CATEGORY": [],
        "REASON": "抓到的是访问拒绝页面。", "RATE": {},
    }
    legacy = EVENT_V4_PROMPT_CATALOG.protocol_for(44).validate_result(legacy_payload)
    assert legacy.NON_INTELLIGENCE_REASON is None

    with pytest.raises(ValueError, match="NON_INTELLIGENCE_REASON"):
        EVENT_V4_PROMPT_CATALOG.protocol_for(46).validate_result(legacy_payload)

    classified = dict(legacy_payload)
    classified["NON_INTELLIGENCE_REASON"] = "anti_bot_or_access_denied"
    result = EVENT_V4_PROMPT_CATALOG.protocol_for(46).validate_result(classified)
    assert result.NON_INTELLIGENCE_REASON.value == "anti_bot_or_access_denied"
