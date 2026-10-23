"""从 PredicateRegistry 编译核心子集版事件抽取 Prompt 章节。

子集协议把机械性内容（schema_version、局部 ID、引用完整性、Frame 查表、
限定词编号）移出 LLM 输出，由服务端桥接层确定性补齐；LLM 只负责语义判断。
存储契约不变：桥接产物仍是完整的 event-extraction/1.0。
"""

from __future__ import annotations

from .prompt import _build_qualifier_catalog, build_predicate_catalog


CORE_ROLE_LANDING_SECTION = """## 角色落点

- 严格区分谁下令、谁执行、谁是对象：“甲要求乙调查丙”中，执行方角色（如 investigator）填乙，对象角色（如 object）填丙，甲只写入 directive 限定词的 by，即 `{"type":"directive","value":"ordered","by":[甲实体]}`。
- 调查、审查、评估的对象必须是真正的客体；“调查某作品的消息源”时 object 是消息源，不得简化为作品本身。
- 实体名称必须保留消歧类别词，如“纪录片《NAZA》”“Rafael天然气项目”，不得只写裸专名“NAZA”。
- 计划、拟议、威胁、要求不是已发生事实：未来或拟议事件必须加限定词（intention=planned 或 phase=planned/not_started），时间使用 expected_start_time 等预期槽位。
- 特定机构作出的判断（如“韩军称其为武力展示”）必须用 epistemic 限定词保留陈述主体，by 填该机构实体。"""

MINIMAL_TIME_RULE = (
    '- `time` 只在能规范化时输出，槽位可选 event_time、start_time、end_time、'
    'effective_time、deadline、expected_start_time、expected_end_time；每个槽位只写 '
    '`{"normalized":"2026-10-17","surface":"原文时间"}`，不要输出 precision 或 approximate。'
)

FULL_TIME_RULE = (
    '- `time` 可使用 event_time、start_time、end_time、effective_time、deadline、expected_start_time、expected_end_time。\n'
    '- 时间包含 normalized、precision、approximate、surface；surface 必须逐字摘自正文（保持原文语言），禁止翻译或概括。precision 与 normalized 必须严格对应：year=`YYYY`、month=`YYYY-MM`、day=`YYYY-MM-DD`、hour=`YYYY-MM-DDTHH`、minute=`YYYY-MM-DDTHH:MM`。不要为 hour/minute 添加秒、时区或空格；不能可靠标准化时 normalized 为 null。'
)


def build_core_extraction_section(registry, *, minimal_time: bool = False) -> str:
    """返回可嵌入外部 Prompt 的核心子集事件抽取章节，不包含输入和外围任务。"""
    time_rule = MINIMAL_TIME_RULE if minimal_time else FULL_TIME_RULE
    return f"""# 事件抽取协议（核心子集）

输出 `event_extraction` 对象，只包含一个 `events` 数组（一至三项事件）。
不要输出 schema_version、entities 列表或任何 ID；实体、事件和限定词的编号由服务端统一分配，同名实体（name 完全一致）会自动合并为同一实体。

每项事件是一个扁平对象：
- `is_primary`：布尔值，是否主事件；全部事件中恰好一项为 true。
- `predicate`：`{{"id":"目录谓词ID或null","surface":"原文措辞"}}`；id 为 null 时必须同时提供 `gloss` 简要说明动作含义，id 非空时禁止 gloss。
- `frame`：仅当 predicate.id 为 null 时必填（见角色规则）；已登记谓词的 Frame 由服务端按目录确定，禁止输出。
- `agency`：仅当目录中该谓词的 FRAME 标注 unknown 时可选填 agentive、non_agentive 或 unknown；其他情况禁止输出。
- `roles`：`{{"角色名":[实体对象,...]}}`，至少一个角色，每个角色至少一个实体。
- `time`、`context`、`attributes`：可选，规则见“时间、地点和属性”。
- `qualifiers`：限定词数组，规则见“限定词”。
- `relations`：事件关系数组，规则见“事件关系”。

实体对象写法：`{{"name":"实体名","type":"类型","country_code":"两位代码或省略"}}`，直接写在 roles、context.event_location 或限定词的 by 中；同一实体在多处出现时保持 name 完全一致。

```json
{{
  "events": [{{
    "is_primary": true,
    "predicate": {{"id":"attack","surface":"袭击"}},
    "roles": {{
      "actor": [{{"name":"某武装组织","type":"organization"}}],
      "target": [{{"name":"乌克兰的发电厂","type":"facility","country_code":"UA"}}]
    }},
    "qualifiers": [],
    "relations": []
  }}]
}}
```

实体 type 只能是 person、organization、geopolitical_entity、location、facility、equipment、product、resource、asset、information、policy、agreement、position、capability、topic、phenomenon、case、event、other_object。country_code 可选，只能使用真实存在的两位大写 ISO 3166-1 代码；虚构国家、代称国家或无法确定时必须省略，禁止编造 XA、XB 等代码。

country_code 用于保留原文明示的国别或地理关联。对于 facility、location、equipment、asset、resource、product 等物理实体，它表示所在或部署国家：例如原文明确写“乌克兰的发电厂”或“位于乌克兰的发电厂”，即使实体名仅为“发电厂”，也必须标注 country_code=UA。对于 person、organization，它只记录原文明示的国别关联。country_code 不表示所有权；不得根据攻击方、其他参与方或无关地点反推目标实体的国家，证据不足时省略。

## 谓词目录

目录来自 Event Engine Registry，是谓词和角色的唯一规则源。`+` 表示必填角色，`?` 表示已登记角色。
无法准确分类时 predicate.id 使用 null 并提供 gloss，不得强行套用近似谓词；否则不得使用目录外 ID。

{build_predicate_catalog(registry)}

## 角色

- 优先使用目录登记角色。所有角色值都是实体对象数组，且不能为空。
- predicate.id 为 null 时必须提供 frame，且 frame.topology 决定可用角色：intrinsic 使用 subject；relational 使用 subject、counterpart；targeted 使用 actor、target 和可选 instrument；transfer 使用 theme、可选 source、destination、agent，并至少包含 source 或 destination。

{CORE_ROLE_LANDING_SECTION}

## 时间、地点和属性

{time_rule}
- `context` 只使用 event_location，值为实体对象数组。决定事件身份的起点、终点、目标等位置应进入 roles。原文明示事件发生在某国时，event_location 应包含相应地点实体；若目标设施等物理实体也明确位于该国，还应同时在该目标实体上填写 country_code，以保留“目标位于哪里”的直接联系。
- `attributes` 必须是以 amount、quantity、ratio、value_before、value_after、delta、duration、level 为键的 JSON 对象，绝不能是数组；每种键最多出现一次。
- amount 使用 `{{"type":"money","value":8500000000,"currency":"CAD","unit":"元","surface":"85亿加元"}}`。
- quantity、value_before、value_after、delta、level 使用 `{{"type":"number","value":5000,"unit":"人","surface":"5000人"}}`。
- ratio 使用 `{{"type":"ratio","value":0.25,"surface":"25%"}}`，value 范围为 0 至 1。
- duration 使用 `{{"type":"duration","value":3,"unit":"年","surface":"三年"}}`。

## 限定词

限定词值来自 Event Engine 的生命周期规则：
{_build_qualifier_catalog(registry)}

每项限定词包含 type、value，以及可选的 by（实体对象数组）、time、surface。epistemic 必须提供 by；phase 和 polarity 禁止 by。限定词一律作用于所在事件本身。

## 事件关系

每项关系使用 `predicate`、`target` 和可选的 `surface`；禁止使用 `type` 字段。
`predicate` 只允许 causes、promotes、prevents、aggravates、mitigates、precedes、follows、overlaps、condition_for、part_of。`target` 是目标事件在 events 数组中的位置序号（从 1 开始），禁止指向自身。
关系挂在关系起点事件上，例如第一项事件导致第二项事件，应把 `{{"predicate":"causes","target":2}}` 放入第一项事件的 relations。
主事件已经完整表达的直接结果（如袭击造成设施受损）通常写入 EVENT_TEXT/IMPACT 即可，不必为了结果重复拆出第二事件；只有结果本身需要独立检索或与其他事件建立关系时才拆分。
"""


def build_core_extraction_prompt(registry) -> str:
    """返回核心子集协议可独立使用的完整事件抽取 Prompt。"""
    return f"""# 角色与任务

你是事件抽取器。只根据当前正文提取一至三项核心事件。
当前参考时间：{{{{CURRENT_DATE}}}}
输出语言：简体中文

# 输入

{{{{CONTENT}}}}

# 输出要求

只输出一个符合核心子集协议的 JSON 对象，不得输出 Markdown、注释或额外文字。不得补充正文没有的事实。

{build_core_extraction_section(registry)}
"""
