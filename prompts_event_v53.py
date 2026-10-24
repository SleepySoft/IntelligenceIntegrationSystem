"""IIS v53：保留 v52 收益，修复角色白名单下的核心参与者完整度。

只重写 v52 的角色纪律：先确定核心参与者再选择谓词；角色无法合法安置时必须
改用开放谓词，不得通过省略实体来满足白名单。其余章节和输出协议保持 v52。
"""

from event_engine.extraction import CORE_ROLE_LANDING_SECTION
from prompts_event_v50 import build_role_discipline
from prompts_event_v52 import (
    build_event_v52_extraction_section,
    build_v52_role_section,
)


CORE_PARTICIPANT_COMPLETENESS_GATE = """- 核心参与者是正文明确提及、且缺少后会改变或严重削弱事件理解的行动方、权威方、对象/目标、承受方，以及转移类事件的来源、去向或转移对象。不得因目录难匹配、角色名不确定或希望减少输出而省略核心参与者。
- 对每项事件分别核对：正文明确的核心参与者是否都已出现在语义合适的角色中；是否误把核心参与者留成未绑定实体；是否为了凑齐角色而补入正文未提及的实体。roles 在 schema 中可选，只表示正文未支持时可以没有，不表示正文明确时可以省略。
- 谓词目录用于规范表达，不用于限制可抽取的事实。已登记谓词只有在谓词含义及全部核心参与者的角色落点都合适时才使用。
- 若核心参与者无法合法、准确地放入当前已登记谓词，必须改用 predicate.id=null 的开放谓词和相应 frame，保留参与者；不得省略参与者，不得改塞入语义不符的角色，也不得为了使用开放谓词而删除事件。开放谓词是正常表达路径，不是低质量兜底。
- 非核心背景实体可按事件选择需要去重或省略，但“角色不匹配”不是省略理由。"""

_V52_ENTITY_FALLBACK = "- 主事件的行动方、对象等核心参与者不得为了满足白名单而省略；若核心参与者无法放入当前谓词的合法角色，必须改用 predicate.id=null 的开放谓词。只有不影响事件事实的非核心实体才可省略；禁止为了保留已登记谓词而把实体塞进语义不符的合法角色。\n"


def build_v53_role_section(registry) -> str:
    """以参与者完整度为先，保留 Registry 白名单、类型和角色落点纪律。"""
    discipline = build_role_discipline(
        registry, protect_core_entities=True,
    ).replace("### 已登记谓词的角色白名单（输出前逐项检查）\n\n", "", 1)
    if discipline.count(_V52_ENTITY_FALLBACK) != 1:
        raise ValueError("v52 role discipline has unexpected entity fallback")
    discipline = discipline.replace(_V52_ENTITY_FALLBACK, "", 1)
    return """## 角色与参与者完整度（输出前逐事件检查）

所有角色值都是非空实体对象数组。先从正文确定事件事实和核心参与者，再选择谓词；不得先选谓词，再按其 roles 删减正文事实。

""" + CORE_PARTICIPANT_COMPLETENESS_GATE + """

### 已登记谓词的角色白名单

""" + discipline + """- 无法匹配时按上节改用开放谓词，而不是自创角色或删减核心参与者。
- predicate.id=null 时必须提供 frame；frame.topology 限定角色：intrinsic 用 subject；relational 用 subject、counterpart；targeted 用 actor、target，可选 instrument；transfer 用 theme，可选 source、destination、agent，且至少有 source 或 destination。

""" + CORE_ROLE_LANDING_SECTION + "\n"


def build_event_v53_extraction_section(registry) -> str:
    """仅替换 v52 的完整角色段，时间门和其他抽取规则逐字保留。"""
    section = build_event_v52_extraction_section(registry)
    old_role_section = build_v52_role_section(registry)
    if section.count(old_role_section) != 1:
        raise ValueError("v52 extraction section has unexpected role section")
    return section.replace(old_role_section, build_v53_role_section(registry), 1)


def build_event_v53_prompt(base_v52_prompt: str, base_v52_extraction: str, registry) -> str:
    """在完整 v52 Prompt 中仅替换参与者完整度修复后的抽取章节。"""
    replacement = build_event_v53_extraction_section(registry)
    if base_v52_prompt.count(base_v52_extraction) != 1:
        raise ValueError("v52 prompt has unexpected extraction section")
    return base_v52_prompt.replace(base_v52_extraction, replacement, 1)
