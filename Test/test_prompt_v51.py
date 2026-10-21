"""v51 只替换 v48 谓词/角色章节的回归测试。"""

from copy import deepcopy

import prompts_event_v4 as catalog_module
import prompts_event_v50 as v50
from event_engine.domains import registry_for
from prompts_event_v51 import (
    build_event_v51_extraction_section,
    build_event_v51_prompt,
)
from test_prompt_v49 import _output_example, _rich_output


def test_v51_replaces_v50_in_production_gray_pair():
    catalog = catalog_module.EVENT_V4_PROMPT_CATALOG
    assert catalog.sampling_versions() == [48, 51]
    assert catalog.protocol_for(51) is catalog.protocol_for(48)
    assert catalog.text_for(51) == catalog_module.EVENT_ANALYSIS_PROMPT_V51
    assert catalog_module.EVENT_ANALYSIS_PROMPT_TABLE == {
        48: catalog_module.EVENT_ANALYSIS_PROMPT_V48,
        51: catalog_module.EVENT_ANALYSIS_PROMPT_V51,
    }
    assert catalog.extra_table()[50] == v50.EVENT_ANALYSIS_PROMPT_V50


def test_v51_only_replaces_v48_extraction_section():
    replacement = build_event_v51_extraction_section(
        catalog_module.DEFAULT_EVENT_REGISTRY)
    expected = catalog_module.EVENT_ANALYSIS_PROMPT_V48.replace(
        catalog_module.EVENT_CORE_EXTRACTION_SECTION_MINIMAL_TIME,
        replacement,
        1,
    )
    prompt = catalog_module.EVENT_ANALYSIS_PROMPT_V51
    assert prompt == expected
    assert len(prompt) < len(catalog_module.EVENT_ANALYSIS_PROMPT_V48) * 0.75
    assert '"normalized":"2026-10-17","surface":"原文时间"' in prompt
    assert "主事件已经完整表达的直接结果" in prompt
    assert "信息状态与阶段（必须保留）" in prompt
    assert "无情报价值原因分类（必须遵守）" in prompt


def test_v51_compacts_catalog_and_keeps_role_discipline():
    prompt = catalog_module.EVENT_ANALYSIS_PROMPT_V51
    assert "公共可选角色：" in prompt
    assert "每个谓词的允许角色为公共角色与该行 roles 的并集" in prompt
    assert "角色纪律（输出前逐项检查）" in prompt
    assert "两处都没有的角色绝对禁止输出" in prompt
    assert "语义组名也不是输出角色" in prompt
    assert "政府机关、军队、企业、社会团体是 organization" in prompt
    assert "frame.topology 限定角色" in prompt


def test_v51_selected_registry_controls_compact_catalog_and_discipline():
    registry = registry_for("news")
    prompt = build_event_v51_prompt(
        catalog_module.EVENT_ANALYSIS_PROMPT_V48,
        catalog_module.EVENT_CORE_EXTRACTION_SECTION_MINIMAL_TIME,
        registry,
    )
    assert "- attack |" in prompt
    assert "- build_facility |" not in prompt
    assert "- issue_bond |" not in prompt
    assert "sender" in prompt


def test_v51_keeps_v48_output_protocol_compatible():
    catalog = catalog_module.EVENT_V4_PROMPT_CATALOG
    for payload in (_output_example(), _rich_output()):
        expected = catalog.protocol_for(48).validate_result(deepcopy(payload))
        actual = catalog.protocol_for(51).validate_result(deepcopy(payload))
        assert actual.model_dump() == expected.model_dump()
