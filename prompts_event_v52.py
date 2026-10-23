"""IIS v52：修复 v51 紧凑目录下的时间与核心实体完整度。

保留 v51 的紧凑谓词目录和角色白名单；完整恢复 v48 的角色落点规则，禁止为满足
白名单而省略核心参与者，并把可可靠规范化的事件时间从 schema 可选明确为条件必填。
"""

from event_engine.extraction import (
    CORE_ROLE_LANDING_SECTION,
    MINIMAL_TIME_RULE,
)
from prompts_event_v50 import build_role_discipline
from prompts_event_v51 import (
    build_compact_role_section,
    build_event_v51_extraction_section,
)


TIME_COMPLETENESS_GATE = (
    "- 对每项事件分别检查正文是否明示其发生、开始、结束、生效、截止或预期时间。"
    "若能可靠规范化，`time` 对该事件为必填；只有正文没有事件时间或无法可靠规范化时才可省略。"
    "报道发布时间不能冒充事件时间。已发生事件使用 event_time、start_time、end_time 或 effective_time；"
    "计划或预期事件使用 expected_start_time、expected_end_time，截止要求使用 deadline；"
    "surface 必须逐字来自正文。"
)


def build_v52_role_section(registry) -> str:
    """使用 v51 白名单纪律，同时完整保留 v48 的角色落点规则。"""
    discipline = build_role_discipline(
        registry, protect_core_entities=True,
    ).replace(
        "### 已登记谓词的角色白名单（输出前逐项检查）\n\n", "", 1,
    )
    return """## 角色纪律（输出前逐项检查）

所有角色值都是非空实体对象数组。

""" + discipline + """
- predicate.id=null 时必须提供 frame；frame.topology 限定角色：intrinsic 用 subject；relational 用 subject、counterpart；targeted 用 actor、target，可选 instrument；transfer 用 theme，可选 source、destination、agent，且至少有 source 或 destination。

""" + CORE_ROLE_LANDING_SECTION + "\n"


def build_event_v52_extraction_section(registry) -> str:
    """只替换 v51 的命名角色段，并在既有最小时间规则前增加检查门。"""
    section = build_event_v51_extraction_section(registry)
    old_role_section = build_compact_role_section(registry)
    if section.count(old_role_section) != 1:
        raise ValueError("v51 extraction section has unexpected role section")
    section = section.replace(old_role_section, build_v52_role_section(registry), 1)
    if section.count(MINIMAL_TIME_RULE) != 1:
        raise ValueError("v51 extraction section has unexpected minimal time rule")
    return section.replace(
        MINIMAL_TIME_RULE,
        TIME_COMPLETENESS_GATE + "\n" + MINIMAL_TIME_RULE,
        1,
    )


def build_event_v52_prompt(base_v51_prompt: str, base_v51_extraction: str, registry) -> str:
    """在完整 v51 Prompt 中仅替换完整度修复后的抽取章节。"""
    if base_v51_prompt.count(base_v51_extraction) != 1:
        raise ValueError("v51 prompt has unexpected extraction section")
    return base_v51_prompt.replace(
        base_v51_extraction, build_event_v52_extraction_section(registry), 1,
    )
