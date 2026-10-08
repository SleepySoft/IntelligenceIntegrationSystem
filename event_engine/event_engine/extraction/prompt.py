"""从 PredicateRegistry 编译可独立使用的事件抽取 Prompt。"""

from __future__ import annotations

from .models import NON_LIFECYCLE_QUALIFIER_VALUES


def _format_frame(frame) -> str:
    return "/".join((frame.dynamics.value, frame.topology.value, frame.agency.value))


def build_predicate_catalog(registry) -> str:
    lines = []
    for predicate_id, spec in registry.items():
        frames = spec.allowed_frames or ((spec.frame,) if spec.frame is not None else ())
        frame_text = " | ".join(_format_frame(frame) for frame in frames) or "open"
        required = set(spec.required_roles)
        roles = ",".join(
            f"{role}{'+' if role in required else '?'}" for role in sorted(spec.role_groups)
        ) or "open"
        label = spec.label or predicate_id
        definition = spec.definition or label
        lines.append(
            f"- {predicate_id} | {label} | {definition} | FRAME={frame_text} | roles={roles}"
        )
    return "\n".join(lines)


def _build_qualifier_catalog(registry) -> str:
    values: dict[str, set[str]] = {
        key: set(items) for key, items in NON_LIFECYCLE_QUALIFIER_VALUES.items()
    }
    for spec in registry.values():
        if not spec.lifecycle:
            continue
        for qualifier_type, transitions in spec.lifecycle.transitions.items():
            values.setdefault(qualifier_type, set()).update(
                value for edge in transitions for value in edge
            )
    return "\n".join(
        f"- {qualifier_type}: {' | '.join(sorted(items))}"
        for qualifier_type, items in sorted(values.items())
    )


def build_event_extraction_section(registry) -> str:
    """返回可嵌入外部 Prompt 的事件抽取章节，不包含输入和外围任务。"""
    return f"""# 事件抽取协议

输出 `event_extraction` 对象，结构为：
- `schema_version`: 固定为 `event-extraction/1.0`
- `primary_event_id`: 主事件局部 ID
- `entities`: 本次输出引用的局部实体，ID 使用 ENT1、ENT2……
- `events`: 一至三项事件，ID 使用 E1、E2……

每项事件包含 `core.frame`、`core.predicate`、`core.roles`，以及可选的 time、context、attributes、qualifiers、relations。
所有实体、事件和限定词引用必须指向本次输出内已声明的局部 ID。

```json
{{
  "schema_version": "event-extraction/1.0",
  "primary_event_id": "E1",
  "entities": [{{"id":"ENT1","name":"实体名","type":"organization","country_code":"CN"}}],
  "events": [{{
    "id": "E1",
    "core": {{
      "frame": {{"dynamics":"process","topology":"targeted","agency":"agentive"}},
      "predicate": {{"id":"attack","surface":"袭击"}},
      "roles": {{"actor":["ENT1"],"target":["ENT2"]}}
    }},
    "qualifiers": [],
    "relations": []
  }}]
}}
```

实体 type 只能是 person、organization、geopolitical_entity、location、facility、equipment、product、resource、asset、information、policy、agreement、position、capability、topic、phenomenon、case、event、other_object。country_code 可选，使用两位大写 ISO 代码。

## 谓词目录

目录来自 Event Engine Registry，是谓词、Frame 和角色的唯一规则源。`+` 表示必填角色，`?` 表示已登记角色。
无法准确分类时 predicate.id 使用 null，并提供 gloss；否则不得使用目录外 ID。

{build_predicate_catalog(registry)}

## Frame 与角色

- Frame 必须符合谓词目录；Registry 中 agency=unknown 时，可按正文输出 agentive、non_agentive 或 unknown。
- 优先使用目录登记角色。所有角色值都是实体 ID 数组，且不能为空。
- predicate.id 为 null 时：intrinsic 使用 subject；relational 使用 subject、counterpart；targeted 使用 actor、target 和可选 instrument；transfer 使用 theme、可选 source、destination、agent，并至少包含 source 或 destination。

## 时间、地点和属性

- time 可使用 event_time、start_time、end_time、effective_time、deadline、expected_start_time、expected_end_time。
- 时间包含 normalized、precision、approximate、surface；precision 使用 year、month、day、hour、minute，不能可靠标准化时 normalized 为 null。
- context 只使用 event_location。决定事件身份的起点、终点、目标等位置应进入 roles。
- attributes 只使用 amount、quantity、ratio、value_before、value_after、delta、duration、level。

## 限定词

限定词值同样来自 Event Engine 的生命周期规则：
{_build_qualifier_catalog(registry)}

每项限定词包含 id、type、value、scope，以及可选的 by、time、surface。epistemic 必须提供 by；phase 和 polarity 禁止 by。scope 为 event 或同一事件中更早出现的限定词 ID，最多嵌套一层。

## 事件关系

关系只允许 causes、promotes、prevents、aggravates、mitigates、precedes、follows、overlaps、condition_for、part_of。target_event_id 必须引用本次输出中的另一事件，禁止自指。
"""


def build_event_extraction_prompt(registry) -> str:
    """返回 Event Engine 可独立使用的完整事件抽取 Prompt。"""
    return f"""# 角色与任务

你是事件抽取器。只根据当前正文提取一至三项核心事件。
当前参考时间：{{{{CURRENT_DATE}}}}
输出语言：简体中文

# 输入

{{{{CONTENT}}}}

# 输出要求

只输出一个符合 EventExtractionResult 的 JSON 对象，不得输出 Markdown、注释或额外文字。不得补充正文没有的事实。

{build_event_extraction_section(registry)}
"""
