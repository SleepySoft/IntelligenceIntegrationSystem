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


def build_compact_predicate_catalog(registry) -> str:
    """按完整 Frame 分组、提取公共可选角色；不删减 Registry 的任何目录信息。

    只提取所有谓词共有且均非必填的角色，避免把某一谓词的必填条件推广到全局。
    分组键保留 allowed_frames 的顺序，因为核心子集桥接使用第一个 Frame。
    """
    items = list(registry.items())
    if not items:
        return "（无已登记谓词；使用开放谓词。）"
    common_roles = set.intersection(*(
        set(spec.role_groups) - set(spec.required_roles) for _, spec in items
    ))
    groups: dict[tuple[str, ...], list[str]] = {}
    for predicate_id, spec in items:
        frames = spec.allowed_frames or ((spec.frame,) if spec.frame is not None else ())
        frame_key = tuple(_format_frame(frame) for frame in frames)
        roles = ",".join(
            f"{role}{'+' if role in spec.required_roles else '?'}"
            for role in sorted(set(spec.role_groups) - common_roles)
        ) or "无"
        label = spec.label or predicate_id
        definition = spec.definition or label
        groups.setdefault(frame_key, []).append(
            f"- {predicate_id} | {label} | {definition} | roles={roles}"
        )
    lines = [
        "公共可选角色：" + (",".join(sorted(common_roles)) or "无"),
        "每个谓词的允许角色为公共角色与该行 roles 的并集；"
        "+ 表示必填，? 表示可选，roles=无表示没有额外角色。",
        "每组标题列出组内谓词的完整 Frame（dynamics/topology/agency）；"
        "多个 Frame 按目录顺序以 | 分隔。列格式：ID | 名称 | 定义 | roles。",
    ]
    for frames, rows in groups.items():
        lines.extend(("", "### FRAME=" + (" | ".join(frames) or "open"), *rows))
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

每项事件根层只能包含 `id`、`core`、`qualifiers`、`relations`。
`core` 必须包含 `frame`、`predicate`、`roles`，并可选包含 `time`、`context`、`attributes`；
禁止把 `time`、`context`、`attributes` 放在事件根层。
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

实体 type 只能是 person、organization、geopolitical_entity、location、facility、equipment、product、resource、asset、information、policy、agreement、position、capability、topic、phenomenon、case、event、other_object。country_code 可选，只能使用真实存在的两位大写 ISO 3166-1 代码；虚构国家、代称国家或无法确定时必须省略，禁止编造 XA、XB 等代码。

country_code 用于保留原文明示的国别或地理关联。对于 facility、location、equipment、asset、resource、product 等物理实体，它表示所在或部署国家：例如原文明确写“乌克兰的发电厂”或“位于乌克兰的发电厂”，即使实体名仅为“发电厂”，也必须标注 country_code=UA。对于 person、organization，它只记录原文明示的国别关联。country_code 不表示所有权；不得根据攻击方、其他参与方或无关地点反推目标实体的国家，证据不足时省略。

## 谓词目录

目录来自 Event Engine Registry，是谓词、Frame 和角色的唯一规则源。`+` 表示必填角色，`?` 表示已登记角色。
无法准确分类时 predicate.id 使用 null，并提供 gloss；否则不得使用目录外 ID。

{build_predicate_catalog(registry)}

## Frame 与角色

- Frame 必须逐项复制谓词目录允许的完整三元组，不能凭语感修改其中任何一项；例如 damage 只能使用目录所列 change/targeted/agentive。仅当目录写 agency=unknown 时，才可按正文输出 agentive、non_agentive 或 unknown。
- 优先使用目录登记角色。所有角色值都是实体 ID 数组，且不能为空。
- predicate.id 为 null 时：intrinsic 使用 subject；relational 使用 subject、counterpart；targeted 使用 actor、target 和可选 instrument；transfer 使用 theme、可选 source、destination、agent，并至少包含 source 或 destination。

## 时间、地点和属性

- `core.time` 可使用 event_time、start_time、end_time、effective_time、deadline、expected_start_time、expected_end_time。
- 时间包含 normalized、precision、approximate、surface；precision 与 normalized 必须严格对应：year=`YYYY`、month=`YYYY-MM`、day=`YYYY-MM-DD`、hour=`YYYY-MM-DDTHH`、minute=`YYYY-MM-DDTHH:MM`。不要为 hour/minute 添加秒、时区或空格；不能可靠标准化时 normalized 为 null。
- `core.context` 只使用 event_location。决定事件身份的起点、终点、目标等位置应进入 roles。原文明示事件发生在某国时，event_location 应包含相应地点实体；若目标设施等物理实体也明确位于该国，还应同时在该目标实体上填写 country_code，以保留“目标位于哪里”的直接联系。
- `core.attributes` 必须是以 amount、quantity、ratio、value_before、value_after、delta、duration、level 为键的 JSON 对象，绝不能是数组；每种键最多出现一次。
- amount 使用 `{{"type":"money","value":8500000000,"currency":"CAD","unit":"元","surface":"85亿加元"}}`。
- quantity、value_before、value_after、delta、level 使用 `{{"type":"number","value":5000,"unit":"人","surface":"5000人"}}`。
- ratio 使用 `{{"type":"ratio","value":0.25,"surface":"25%"}}`，value 范围为 0 至 1。
- duration 使用 `{{"type":"duration","value":3,"unit":"年","surface":"三年"}}`。

## 限定词

限定词值同样来自 Event Engine 的生命周期规则：
{_build_qualifier_catalog(registry)}

每项限定词包含 id、type、value、scope，以及可选的 by、time、surface。epistemic 必须提供 by；phase 和 polarity 禁止 by。scope 为 event 或同一事件中更早出现的限定词 ID，最多嵌套一层。

## 事件关系

每项关系使用 `predicate`、`target_event_id` 和可选的 `surface`；禁止使用 `type` 字段。
`predicate` 只允许 causes、promotes、prevents、aggravates、mitigates、precedes、follows、overlaps、condition_for、part_of。`target_event_id` 必须引用本次输出中的另一事件，禁止自指。
关系挂在关系起点事件上，例如 E1 导致 E2，应把 `{{"predicate":"causes","target_event_id":"E2"}}` 放入 E1 的 relations；不要放入 E2，也不要写成 E2 指向自身。
主事件已经完整表达的直接结果（如袭击造成设施受损）通常写入 EVENT_TEXT/IMPACT 即可，不必为了结果重复拆出第二事件；只有结果本身需要独立检索或与其他事件建立关系时才拆分。
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
