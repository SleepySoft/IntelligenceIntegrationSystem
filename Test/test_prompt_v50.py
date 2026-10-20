"""v50 角色白名单与实体类型约束回归。"""

from copy import deepcopy

import prompts_event_v4 as catalog_module
import prompts_event_v49 as v49
import prompts_event_v50 as v50
from event_engine.domains import registry_for
from test_prompt_v49 import _output_example, _rich_output


def test_v50_replaces_v49_in_production_gray_pair():
    catalog = catalog_module.EVENT_V4_PROMPT_CATALOG
    assert catalog.sampling_versions() == [48, 50]
    assert catalog.protocol_for(50) is catalog.protocol_for(48)
    assert catalog.text_for(50) == v50.EVENT_ANALYSIS_PROMPT_V50
    assert catalog_module.EVENT_ANALYSIS_PROMPT_TABLE == {
        48: catalog_module.EVENT_ANALYSIS_PROMPT_V48,
        50: v50.EVENT_ANALYSIS_PROMPT_V50,
    }


def test_v50_preserves_v49_and_adds_explicit_registered_role_checks():
    prompt = v50.EVENT_ANALYSIS_PROMPT_V50
    assert v49.EVENT_ANALYSIS_PROMPT_V49 not in ("", prompt)
    assert "已登记谓词的角色白名单" in prompt
    assert "两处都没有的角色绝对禁止输出" in prompt
    assert "语义组名也不是输出角色" in prompt
    assert "政府机关、军队、企业、社会团体是 organization" in prompt
    assert len(prompt) < len(catalog_module.EVENT_ANALYSIS_PROMPT_V48) * 0.75


def test_v50_role_groups_are_derived_from_selected_registry():
    registry = registry_for("news")
    discipline = v50.build_role_discipline(registry)
    assert "actor" in discipline
    assert "authority" in discipline
    assert "sender" in discipline
    assert "topic 角色禁止使用 person 或 animal" in discipline


def test_v50_keeps_v48_v49_output_protocol_compatible():
    catalog = catalog_module.EVENT_V4_PROMPT_CATALOG
    for payload in (_output_example(), _rich_output()):
        expected = catalog.protocol_for(48).validate_result(deepcopy(payload))
        actual = catalog.protocol_for(50).validate_result(deepcopy(payload))
        assert actual.model_dump() == expected.model_dump()
