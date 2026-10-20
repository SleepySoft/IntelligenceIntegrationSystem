"""独立编写的核心子集精简说明；只依赖契约和 Registry，不改写历史 Prompt。

保留核心子集及最小时间输出语义。IIS 外层字段、评分与价值判断由宿主负责；
信息状态与角色落点在本章节集中说明，避免宿主继续追加历史补丁。
"""

from typing import get_args

from .models import EntityType, ExtractedAttributes, ExtractedFrame
from .prompt import _build_qualifier_catalog
from .subset import RelationPredicate, SubsetTime


# 展示对象值，供宿主嵌入自己的完整输出示例；不复制抽取字段到宿主模板。
CORE_EXTRACTION_EXAMPLE = """{
    "events": [{
      "is_primary": true,
      "predicate": {"id":"attack","surface":"袭击"},
      "roles": {
        "actor": [{"name":"某武装组织","type":"organization"}],
        "target": [{"name":"乌克兰的发电厂","type":"facility","country_code":"UA"}]
      },
      "qualifiers": [],
      "relations": []
    }]
  }"""


def build_compact_core_extraction_section(registry) -> str:
    """直接组织规则章节，枚举来自现行契约，限定词取值来自 Registry。"""
    entity_types = "、".join(get_args(EntityType))
    time_slots = "、".join(SubsetTime.model_fields)
    attribute_keys = "、".join(ExtractedAttributes.model_fields)
    relation_predicates = "、".join(get_args(RelationPredicate))
    frame_fields = "；".join(
        f"{name}=" + "/".join(get_args(field.annotation))
        for name, field in ExtractedFrame.model_fields.items()
    )
    return f"""# 事件抽取（核心子集）

## 事件结构与选择

`EVENT_EXTRACTION` 只含 events 数组，提取一至三项核心事件，恰好一项 is_primary=true。
每项事件为扁平对象：
- is_primary：布尔值，标识主事件。
- predicate：id 为目录中的谓词 ID 或 null；surface 为原文措辞。id=null 时必须有 gloss 简述动作含义，id 非空时禁止 gloss。
- frame：仅开放谓词（id=null）必填，含 {frame_fields}。已登记谓词禁止输出 frame，由服务端按目录补齐。
- agency：仅已登记谓词的 Frame 中 agency=unknown 时可选填 agentive、non_agentive 或 unknown；其他情况禁止输出。
- roles：角色名到实体对象数组的映射，至少一个角色，每个角色至少一个实体。
- time、context、attributes：可选对象；qualifiers、relations：数组，规则见下文。

不要输出 schema_version、entities 列表或实体/事件/限定词的局部 ID；这些编号由服务端分配。
主事件的直接结果（如袭击造成设施受损）通常写入简报与影响说明即可；只有结果需要独立检索或与其他事件建立关系时才另拆事件。

## 实体与角色

实体写作 {{"name":"实体名","type":"类型","country_code":"两位代码或省略"}}，内联于 roles、context.event_location 或限定词 by。
- type 仅允许：{entity_types}。
- 同一实体各处 name 必须完全一致，服务端按同名合并；保留消歧类别词，如“纪录片《NAZA》”“Rafael天然气项目”，不得只写“NAZA”。
- 优先使用目录登记角色；无法准确匹配谓词时使用 id=null 并提供 gloss，不得强套近似谓词或编造目录外 ID。
- 开放谓词的 frame.topology 决定角色：intrinsic 用 subject；relational 用 subject、counterpart；targeted 用 actor、target，可选 instrument；transfer 用 theme，可选 source、destination、agent，且至少有 source 或 destination。目录公共角色不适用于开放谓词。
- 严格区分下令方、执行方、对象：“甲要求乙调查丙”中 investigator=乙，object=丙，甲只放入 directive 限定词的 by：{{"type":"directive","value":"ordered","by":[甲实体]}}。
- 调查、审查、评估的对象必须是真正客体；“调查某作品的消息源”中 object 是消息源，不是作品。

## 时间与地点

- time 只在能规范化时输出，可用槽位：{time_slots}；每个槽位只写 {{"normalized":"2026-10-17","surface":"原文时间"}}，不要输出 precision 或 approximate。
- context 只含 event_location（实体对象数组）；决定事件身份的起点、终点、目标等位置进入 roles。
- country_code 只能为真实存在的两位大写 ISO 3166-1 代码；虚构国家、代称或证据不足时省略，禁止编造 XA、XB。
- country_code 保留原文明示的国别或地理关联：facility、location、equipment、asset、resource、product 等物理实体表示所在或部署国家；person、organization 表示原文明示的国别关联，不表示所有权。不得根据攻击方、其他参与方或无关地点反推目标国别。
- 原文明示事件发生国时，event_location 包含对应地点；目标设施也明确位于该国时，还须在目标实体填写 country_code。例如“乌克兰的发电厂”即使命名为“发电厂”，仍须 country_code=UA。

## 数值属性

attributes 是 JSON 对象，禁止数组；允许键 {attribute_keys}，每键最多一次：
- amount：{{"type":"money","value":8500000000,"currency":"CAD","unit":"元","surface":"85亿加元"}}。
- quantity、value_before、value_after、delta、level：{{"type":"number","value":5000,"unit":"人","surface":"5000人"}}。
- ratio：{{"type":"ratio","value":0.25,"surface":"25%"}}，value 在 0 至 1 之间。
- duration：{{"type":"duration","value":3,"unit":"年","surface":"三年"}}。

## 限定词与信息状态

每项限定词含 type、value，可选 by（实体对象数组）、time、surface；只作用于所在事件。
epistemic 必须有 by，phase 和 polarity 禁止 by。取值来自 Registry：
{_build_qualifier_catalog(registry)}

- 事件必须标注 phase（planned/ongoing/completed 等），不得将进行中或计划中事件写成已完成事实。
- 计划、拟议、威胁、要求不是已发生事实：未来或拟议事件加 intention=planned 或 phase=planned/not_started，时间用 expected_start_time 等预期槽位。
- 特定机构的判断（如“韩军称其为武力展示”）用 epistemic 保留陈述主体，by 填该机构。
- 未证实、未回应、否认、拒绝置评必须保留：用 epistemic=doubted 或 denied，by 填该方；“截至发稿未证实”用 doubted。
- 未发生、被取消、被否认等否定信息用 polarity=negated，不得省略。

## 事件关系

每项关系含 predicate、target，可选 surface，禁止 type 字段。
predicate 仅允许 {relation_predicates}；target 为目标事件在 events 中从 1 开始的序号，禁止自指。
关系写在起点事件上：第一项导致第二项时，在第一项的 relations 写 {{"predicate":"causes","target":2}}。
"""
