"""v53 核心参与者完整度修复与生产灰度回归。"""

from copy import deepcopy

import prompts_event_v4 as catalog_module
from event_engine.domains import registry_for
from prompts_event_v52 import TIME_COMPLETENESS_GATE, build_event_v52_prompt
from prompts_event_v53 import (
    CORE_PARTICIPANT_COMPLETENESS_GATE,
    build_event_v53_extraction_section,
    build_event_v53_prompt,
)
from test_prompt_v49 import _output_example, _rich_output


def test_v53_replaces_v52_in_production_gray_pair():
    catalog = catalog_module.EVENT_V4_PROMPT_CATALOG
    assert catalog.sampling_versions() == [48, 53]
    assert catalog.protocol_for(53) is catalog.protocol_for(48)
    assert catalog.text_for(53) == catalog_module.EVENT_ANALYSIS_PROMPT_V53
    assert catalog_module.EVENT_ANALYSIS_PROMPT_TABLE == {
        48: catalog_module.EVENT_ANALYSIS_PROMPT_V48,
        53: catalog_module.EVENT_ANALYSIS_PROMPT_V53,
    }
    assert catalog.extra_table()[52] == catalog_module.EVENT_ANALYSIS_PROMPT_V52


def test_v53_only_replaces_v52_role_section():
    replacement = build_event_v53_extraction_section(
        catalog_module.DEFAULT_EVENT_REGISTRY)
    expected = catalog_module.EVENT_ANALYSIS_PROMPT_V52.replace(
        catalog_module.EVENT_EXTRACTION_SECTION_V52,
        replacement,
        1,
    )
    prompt = catalog_module.EVENT_ANALYSIS_PROMPT_V53
    assert prompt == expected
    assert len(prompt) < len(catalog_module.EVENT_ANALYSIS_PROMPT_V48) * 0.75
    assert len(prompt) - len(catalog_module.EVENT_ANALYSIS_PROMPT_V52) < 500
    assert TIME_COMPLETENESS_GATE in prompt


def test_v53_protects_participants_before_predicate_selection():
    prompt = catalog_module.EVENT_ANALYSIS_PROMPT_V53
    assert CORE_PARTICIPANT_COMPLETENESS_GATE in prompt
    assert "先从正文确定事件事实和核心参与者，再选择谓词" in prompt
    assert "不得先选谓词，再按其 roles 删减正文事实" in prompt
    assert "必须改用 predicate.id=null 的开放谓词" in prompt
    assert "开放谓词是正常表达路径" in prompt
    assert "只有不影响事件事实的非核心实体才可省略" not in prompt
    assert "就省略该实体或改用 predicate.id=null" not in prompt
    for rule in (
        "两处都没有的角色绝对禁止输出",
        "政府机关、军队、企业、社会团体是 organization",
        "frame.topology 限定角色",
        "严格区分谁下令、谁执行、谁是对象",
        "调查、审查、评估的对象必须是真正的客体",
        "实体名称必须保留消歧类别词",
        "时间使用 expected_start_time 等预期槽位",
        "必须用 epistemic 限定词保留陈述主体",
    ):
        assert rule in prompt


def test_v53_selected_registry_controls_compact_catalog_and_discipline():
    registry = registry_for("news")
    prompt = build_event_v53_prompt(
        build_event_v52_prompt(
            catalog_module.EVENT_ANALYSIS_PROMPT_V51,
            catalog_module.EVENT_EXTRACTION_SECTION_V51,
            registry,
        ),
        catalog_module.build_event_v52_extraction_section(registry),
        registry,
    )
    assert "- attack |" in prompt
    assert "- build_facility |" not in prompt
    assert "- issue_bond |" not in prompt
    assert "sender" in prompt
    assert TIME_COMPLETENESS_GATE in prompt


def test_v53_keeps_v48_output_protocol_compatible():
    catalog = catalog_module.EVENT_V4_PROMPT_CATALOG
    for payload in (_output_example(), _rich_output()):
        expected = catalog.protocol_for(48).validate_result(deepcopy(payload))
        actual = catalog.protocol_for(53).validate_result(deepcopy(payload))
        assert actual.model_dump() == expected.model_dump()
