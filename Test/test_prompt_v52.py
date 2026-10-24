"""v52 紧凑目录完整度修复与生产抽样回归。"""

from copy import deepcopy

import prompts_event_v4 as catalog_module
from event_engine.domains import registry_for
from prompts_event_v52 import (
    TIME_COMPLETENESS_GATE,
    build_event_v52_extraction_section,
    build_event_v52_prompt,
)
from test_prompt_v49 import _output_example, _rich_output


def test_v52_remains_selectable_after_v53_replaces_it_in_gray_sampling():
    catalog = catalog_module.EVENT_V4_PROMPT_CATALOG
    assert catalog.sampling_versions() == [48, 53]
    assert catalog.protocol_for(52) is catalog.protocol_for(48)
    assert catalog.text_for(52) == catalog_module.EVENT_ANALYSIS_PROMPT_V52
    assert catalog_module.EVENT_ANALYSIS_PROMPT_TABLE == {
        48: catalog_module.EVENT_ANALYSIS_PROMPT_V48,
        53: catalog_module.EVENT_ANALYSIS_PROMPT_V53,
    }
    assert catalog.extra_table()[52] == catalog_module.EVENT_ANALYSIS_PROMPT_V52
    assert catalog.extra_table()[51] == catalog_module.EVENT_ANALYSIS_PROMPT_V51


def test_v52_only_replaces_v51_extraction_section():
    replacement = build_event_v52_extraction_section(
        catalog_module.DEFAULT_EVENT_REGISTRY)
    expected = catalog_module.EVENT_ANALYSIS_PROMPT_V51.replace(
        catalog_module.EVENT_EXTRACTION_SECTION_V51,
        replacement,
        1,
    )
    prompt = catalog_module.EVENT_ANALYSIS_PROMPT_V52
    assert prompt == expected
    assert len(prompt) < len(catalog_module.EVENT_ANALYSIS_PROMPT_V48) * 0.75
    assert len(prompt) - len(catalog_module.EVENT_ANALYSIS_PROMPT_V51) < 1000


def test_v52_restores_role_landing_and_conditions_time_output():
    prompt = catalog_module.EVENT_ANALYSIS_PROMPT_V52
    for rule in (
        "严格区分谁下令、谁执行、谁是对象",
        "调查、审查、评估的对象必须是真正的客体",
        "实体名称必须保留消歧类别词",
        "时间使用 expected_start_time 等预期槽位",
        "必须用 epistemic 限定词保留陈述主体",
    ):
        assert rule in prompt
    assert TIME_COMPLETENESS_GATE in prompt
    assert "`time` 对该事件为必填" in prompt
    assert "报道发布时间不能冒充事件时间" in prompt
    assert "核心参与者不得为了满足白名单而省略" in prompt
    assert "只有不影响事件事实的非核心实体才可省略" in prompt
    assert "就省略该实体或改用 predicate.id=null" not in prompt


def test_v52_selected_registry_controls_compact_catalog_and_discipline():
    registry = registry_for("news")
    v51_extraction = catalog_module.build_event_v51_extraction_section(registry)
    v51_prompt = catalog_module.build_event_v51_prompt(
        catalog_module.EVENT_ANALYSIS_PROMPT_V48,
        catalog_module.EVENT_CORE_EXTRACTION_SECTION_MINIMAL_TIME,
        registry,
    )
    prompt = build_event_v52_prompt(v51_prompt, v51_extraction, registry)
    assert "- attack |" in prompt
    assert "- build_facility |" not in prompt
    assert "- issue_bond |" not in prompt
    assert "sender" in prompt
    assert TIME_COMPLETENESS_GATE in prompt


def test_v52_keeps_v48_output_protocol_compatible():
    catalog = catalog_module.EVENT_V4_PROMPT_CATALOG
    for payload in (_output_example(), _rich_output()):
        expected = catalog.protocol_for(48).validate_result(deepcopy(payload))
        actual = catalog.protocol_for(52).validate_result(deepcopy(payload))
        assert actual.model_dump() == expected.model_dump()
