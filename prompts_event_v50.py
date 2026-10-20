"""IIS v50：在 v49 精简结构上收紧已登记谓词的角色与实体类型。

v49 的首轮生产灰度显示，模型会把 actor/affected 等通用语义角色
误用到未声明这些角色的已登记谓词，也会把 event/phenomenon/facility 填入
agent/authority 语义组。v50 不改输出契约，只增加可机械核对的输出前检查。
"""

from event_engine.schema import SemanticRoleGroup
from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY
from prompts_event_v49 import build_event_v49_sections


_ENTITY_ROLE_MARKER = "## 实体与角色\n\n"


def _roles_in_groups(registry, *groups: SemanticRoleGroup) -> str:
    selected = {
        role
        for spec in registry.values()
        for role, group in spec.role_groups.items()
        if group in groups
    }
    return ",".join(sorted(selected)) or "无"


def build_role_discipline(registry=DEFAULT_EVENT_REGISTRY) -> str:
    """Registry 驱动的角色白名单与语义类型检查。"""
    acting_roles = _roles_in_groups(
        registry, SemanticRoleGroup.AGENT, SemanticRoleGroup.AUTHORITY)
    location_roles = _roles_in_groups(registry, SemanticRoleGroup.LOCATION)
    return f"""### 已登记谓词的角色白名单（输出前逐项检查）

- predicate.id 非 null 时，roles 的每个键必须精确出现在“公共可选角色”或该谓词行的 roles 中；两处都没有的角色绝对禁止输出。
- actor、agent、subject、affected 等名称不是可跨谓词套用的通用槽位；它们只在当前谓词白名单明示出现时才合法。语义组名也不是输出角色。
- 若实体无法放入当前谓词的合法角色，就省略该实体或改用 predicate.id=null 的开放谓词；不得自创角色。
- 行动/权威类角色（{acting_roles}）的实体 type 只能是 person、organization、geopolitical_entity 或 position。政府机关、军队、企业、社会团体是 organization，不是 facility。
- 事件、现象、政策、设施不得冒充上述行动/权威角色。自然现象作为原因且登记目录无合法槽位时，用 agency=non_agentive 的开放谓词表达。
- 地点类角色（{location_roles}）的实体 type 只能是 location、geopolitical_entity 或 facility；topic 角色禁止使用 person 或 animal。
"""


def build_event_v50_sections(registry=DEFAULT_EVENT_REGISTRY) -> dict[str, str]:
    sections = dict(build_event_v49_sections(registry))
    extraction = sections["extraction"]
    if _ENTITY_ROLE_MARKER not in extraction:
        raise ValueError("v49 extraction section is missing the entity/role marker")
    sections["extraction"] = extraction.replace(
        _ENTITY_ROLE_MARKER,
        _ENTITY_ROLE_MARKER + build_role_discipline(registry) + "\n",
        1,
    )
    return sections


def build_event_v50_prompt(registry=DEFAULT_EVENT_REGISTRY) -> str:
    return "\n\n".join(
        section.strip() for section in build_event_v50_sections(registry).values()
    ) + "\n"


EVENT_ANALYSIS_PROMPT_V50 = build_event_v50_prompt()
