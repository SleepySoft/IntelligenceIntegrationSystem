"""事件引擎的公共数据模型：语义、观察、稳定身份和可解释结果。

使用顺序通常是 EventIR → EventRecord → CanonicalEvent；匹配过程产生
MatchResult，生命周期重算产生 StateProjection。前者不是新闻正文，观察不等于
已核实事实，CanonicalEvent 也不是把相关事件装在一起的专题容器。

本模块只定义数据及轻量访问方法，不加载领域包、数据库或推理算法。dataclass 的
类型标注不会自动校验输入；frozen/slots 仅限制对象字段赋值和动态加字段，不会深度
冻结 Mapping 内部的字典。调用方需避免修改已登记数据，语义校验由 core.Registry
及接入代码执行。UUID 的实体消歧和来源解析由外部系统完成。
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import UUID


class Dynamics(str, Enum):
    """事件随时间的基本形态，供 Frame 结构校验使用，不代表生命周期阶段。

    例如 PROCESS 事件也可以处于 completed；是否完成由 Qualifier.phase 描述。
    """

    # 状态：某种性质或关系成立，如协议有效；不表示它永久不变。
    STATE = "state"
    # 过程：持续发生的活动，如生产、攻击；不据此推断起止时间或完成情况。
    PROCESS = "process"
    # 变化：从一种情形变到另一种，如收购或恢复；不保证现实中已经发生。
    CHANGE = "change"


class Topology(str, Enum):
    """事件参与者之间的结构形态，供 Frame 校验和无配置时的角色展示兜底使用。

    它不定义具体角色名、资产权利或行业含义；这些由 PredicateSpec 声明。
    """

    # 内在形态：主要描述对象自身，如温度上升；并不禁止附加角色或外因。
    INTRINSIC = "intrinsic"
    # 关系形态：对象之间的关系，如成员关系；不自动表示方向、对称性或因果。
    RELATIONAL = "relational"
    # 指向形态：作用于目标，如调查或攻击；目标也不一定遭受负面影响。
    TARGETED = "targeted"
    # 转移形态：存在转移端点或相应结构；不能单凭此值推断所有权改变。
    TRANSFER = "transfer"


class Agency(str, Enum):
    """是否具有施动性，描述事件结构而非主体意图、责任或事实可信度。

    Registry 校验 Frame 时，UNKNOWN 与已知施动性兼容；另两值不同则不兼容。
    """

    # 有施动性：行为由施动主体参与；不等于故意、合法或已知具体主体。
    AGENTIVE = "agentive"
    # 无施动性：如自然过程；不要把“没有查明行为人”直接标成此值。
    NON_AGENTIVE = "non_agentive"
    # 施动性未知：证据不足时保留未知，不以缺失制造明确结构冲突。
    UNKNOWN = "unknown"


class SemanticRoleGroup(str, Enum):
    """把领域角色投影到通用语义组，便于跨谓词查询，而不替代原角色名。

    如 acquirer/actor 可投影为 AGENT。EventEngine 登记时按 PredicateSpec.role_groups
    规范化已配置的绑定；直接写 Repository 不会执行该步骤。AGENT 与 AUTHORITY
    是不同组，当前“实体行动”查询只查 AGENT，不自动包含所有主动相关组。
    """

    # 行动者/发起者；用于 get_entity_actions，不等于语法主语。
    AGENT = "agent"
    # 受作用对象；用于 get_actions_affecting_entity，不自动表示受损。
    AFFECTED = "affected"
    # 共同参与者；用于关系、多方事件，不强行分成行动者和受作用者。
    PARTICIPANT = "participant"
    # 事件谈论、处理或转移的主题对象，如资产；不必是被动承受者。
    THEME = "theme"
    # 来源端，如卖方或迁移起点；不表示情报来源，来源情报见 intelligence_uuid。
    SOURCE = "source"
    # 去向端，如目的地或接收方；不等同于受作用对象。
    DESTINATION = "destination"
    # 实施工具或手段；不因参与事件而自动成为行动者。
    INSTRUMENT = "instrument"
    # 授权、监管或裁决主体；与 Qualifier.by 的限定词主张主体不是同一个字段。
    AUTHORITY = "authority"
    # 受益方；仅表达已声明的角色，不由内核推断收益大小或正负影响。
    BENEFICIARY = "beneficiary"
    # 地点角色；不会自动同步到 location_entity_uuids，接入方应明确填入所需位置。
    LOCATION = "location"
    # 未归类/未配置角色；保留绑定，不猜测其通用语义。
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Frame:
    """事件的三轴结构分类，适用于跨领域的最低限度结构描述。

    与 PredicateSpec.frame/allowed_frames 联动，由 Registry 校验；并不是包含
    所有领域意义的完整类型系统，也不能单独充当现实事件的身份证据。
    """

    # 时间形态；需与谓词声明一致，不用于判断进行中或已完成。
    dynamics: Dynamics
    # 参与者结构；影响无配置时的角色展示兜底，但不能生成缺失角色。
    topology: Topology
    # 施动性；UNKNOWN 可兼容已知取值，不能把未知当作明确无施动性。
    agency: Agency


@dataclass(frozen=True, slots=True)
class Predicate:
    """描述“发生什么”的谓词及原始表述。

    内核按 id 查 Registry、查询和匹配，不能靠 surface/gloss 的相似度判断同一事件。
    不同语言表述可以共享同一规范 ID；ID 的选择与同义词归一化由接入方负责。
    """

    # 规范谓词 ID；None 表示尚未归一化，可保存观察，但无已注册规则时不能自动合并候选。
    id: str | None
    # 原始动作/状态措辞，如“完成收购”；用于展示与追溯，不参与身份评分。
    surface: str
    # 可选释义，便于解释罕见谓词；内核不解析它，也不将其转换成规则。
    gloss: str | None = None


@dataclass(frozen=True, slots=True)
class RoleBinding:
    """一个实体在一次事件中的一条角色绑定。

    同角色的多个参与者用多条绑定表示；同实体也可有多个角色。实体 UUID 必须先由
    外部实体系统归一化，否则跨来源身份比较无法成立。本类型不检查重复绑定。
    """

    # 谓词内部角色名，如 acquirer/asset；与 PredicateSpec 的角色、身份和展示配置对齐。
    role: str
    # 全局实体标识，不是 Event UUID 或来源中的 ENT1；查询与匹配均使用它。
    entity_uuid: UUID
    # 跨谓词通用语义组；默认 OTHER，登记时可由 Registry 中的角色映射修正。
    semantic_group: SemanticRoleGroup = SemanticRoleGroup.OTHER
    # 可选来源内局部实体 ID，用于追溯；不得据此直接跨来源比较实体。
    local_entity_id: str | None = None


@dataclass(frozen=True, slots=True)
class TimeExpression:
    """保留原表述、规范值、精度与近似性的时间表达，不强迫所有时间变成时刻。

    core.temporal 支持 ISO 年、年月、日期、日期时间。年/月/日按范围处理，日期时间
    转 UTC；当前未带时区的可解析值按 UTC 解释，调用方应优先提供显式时区。
    不可解析或 approximate=True 的表达不会提供精确身份/状态时序证据。
    """

    # 可计算 ISO 值，如 2026-09 或 2026-09-30T12:00:00+08:00；None 不回退解析 surface。
    normalized: str | None
    # 精度标记；year/month/day 会截取规范值到相应精度，其他标记目前无额外截取规则。
    precision: str
    # 是否近似，如“约在上月”；True 保留表述，但时间比较/状态有效时间按未知处理。
    approximate: bool
    # 原始时间措辞，用于展示与追溯；不直接用于身份评分或有效时间计算。
    surface: str


@dataclass(frozen=True, slots=True)
class Qualifier:
    """对事件命题的限定：生命周期、主张事实性、否定、模态等。

    例如 phase=completed 与 epistemic=denied 可以分别存在；不能把所有限定词
    当成单一“事件状态”。原限定词由 StateProjection.observations 完整保留。
    """

    # 观察内部限定词标识，如 Q1；不是全局 UUID，完整定位需联合 EventRecord.uuid。
    id: str
    # 限定维度；生命周期支持 phase/intention/authorization/directive，主张门控另见类说明。
    type: str
    # 该维度的值，如 ongoing/approved/denied；生命周期值应出现在配置迁移图顶点中。
    value: str
    # 限定作用域；当前状态投影与主张门控只处理精确值 event，其他作用域仅保留。
    scope: str = "event"
    # 限定词的主张/意图/授权/指令主体 UUID；非一般参与者表，空元组表示未声明主体。
    by: tuple[UUID, ...] = ()
    # 限定生效时间，优先于事件时间；给出但不可解析时保持未知，不用观察到达时间补齐。
    time: TimeExpression | None = None
    # 原始限定措辞，如“据监管机构否认”；用于解释，不改变规范值。
    surface: str | None = None

    # 联动限制：event 作用域的 polarity 非 positive/affirmative/true、epistemic 非
    # asserted/certain/confirmed/verified/factual/known、modality 非 actual/factual/asserted
    # 时，该观察的生命周期不进入肯定状态投影；这些词值仍不意味着系统核实真值。
    # intention/authorization/directive 按 by 分主体投影，phase 当前不按 by 分组。


@dataclass(frozen=True, slots=True)
class EventRelation:
    """从当前观察指向另一事件观察的显式关系，用于关联而非身份合并。

    例如 causes/follows/mitigates。这里只保存有向边，不执行因果证明、反向补边、
    图闭包或目标存在性校验；相关事件不能因为存在关系就被合并成 CanonicalEvent。
    """

    # 关系谓词，不是当前 Event 的 Predicate.id；目前是开放字符串，关系词表由外部约束。
    predicate: str
    # 目标 EventRecord 的全局 UUID，不是局部 EV01，也不是默认指向 CanonicalEvent。
    target_event_uuid: UUID
    # 原始关系描述；用于追溯，不被内核解析为附加事实。
    surface: str | None = None


@dataclass(frozen=True, slots=True)
class EventIR:
    """纯事件语义中间表示，适用于外部抽取输出、内存分析和结构校验。

    没有观察 UUID、来源、到达时间或存储身份；需要登记/追溯时用 EventRecord.from_ir
    包装。与 EventRecord 共享字段定义，不能随意把评分、新闻摘要或领域影响塞进 IR。
    模型允许不完整观察，具体必填角色和结构限制由所选 PredicateSpec 决定。
    """

    # 基本结构分类；与 predicate 的声明联动校验，不能替代具体谓词含义。
    frame: Frame
    # 规范谓词及原表述；决定 Registry 规则选择与候选身份比较。
    predicate: Predicate
    # 完整参与角色；可为空，但配置的 required_roles 非空时可能拒绝登记。
    role_bindings: tuple[RoleBinding, ...]
    # 时间用途→表达，如 event_time/start_time/end_time/effective_time；不同算法选取顺序不同。
    time: Mapping[str, TimeExpression] = field(default_factory=dict)
    # 事件发生/相关地点 UUID，供地点查询和身份比较；不是地理坐标，也不自动从角色推导。
    location_entity_uuids: tuple[UUID, ...] = ()
    # 属性名→结构化值，如 amount→{value,type,unit,currency}；只声明的身份属性参与匹配。
    attributes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    # 原始全部限定词；状态投影会区分生命周期和主张，不用最后一个值覆盖全部含义。
    qualifiers: tuple[Qualifier, ...] = ()
    # 当前事件发出的显式关系边；不直接参与身份评分，也不表示合并成员。
    relations: tuple[EventRelation, ...] = ()

    # 时间联动：身份区间起点取 start_time/event_time/effective_time 中可用值，终点取
    # end_time/event_time，不虚构开放区间的结束。状态有效时间取 Qualifier.time，否则
    # effective_time/event_time/start_time；明确给出但无效时停止回退。预期时间不当作发生时间。
    # 属性限制：当前比较 value/type/currency/unit；不解析 surface，不做单位、币种换算。

    def entities_for_role(self, role: str) -> tuple[UUID, ...]:
        """按精确领域角色名取实体 UUID，适合身份比较或特定谓词的要素读取。

        role 不存在则返回空元组；保留绑定顺序和重复项，不归一化名字、不做实体消歧，
        也不按 semantic_group 扩展查询。需要集合比较时由匹配器自行转换成集合。
        """
        return tuple(x.entity_uuid for x in self.role_bindings if x.role == role)

    def entities_for_group(self, group: SemanticRoleGroup) -> tuple[UUID, ...]:
        """按通用语义组取 UUID，适合跨谓词查询行动者或受作用实体。

        直接读取绑定的 semantic_group，不查 Registry、不重算映射；应先通过配置登记
        或由调用方填好绑定。没有该组返回空元组，保留顺序及重复项。
        """
        return tuple(x.entity_uuid for x in self.role_bindings if x.semantic_group == group)


class MatchDecision(str, Enum):
    """身份证据判断和解析结果；不等于事实真伪标签。

    match 比较单个候选，resolve 综合候选，EventEngine 再持久化或重算，最终决策
    可能进一步修正。NEW_EVENT/DUPLICATE 属于解析流程，不是两条不同观察的相似度等级。
    """

    # 身份可绑定；引擎重算后生命周期值未变化，仍可能新增证据、争议和成员。
    SAME_EVENT = "same_event"
    # 身份可绑定且生命周期投影变化；匹配器初判只是提示，最终由引擎重算确认。
    STATE_UPDATE = "state_update"
    # 同一观察 UUID 已是成员；返回已有聚合，不增加版本，不代表另一观察文字重复。
    DUPLICATE = "duplicate_observation"
    # 对此候选明确不兼容，或证据足够但分数低于复核阈值；不意味着观察无效或全局无候选。
    DIFFERENT = "different_event"
    # 身份证据不足、候选不够领先或更新冲突；引擎返回 None，不强绑也不另造身份。
    AMBIGUOUS = "ambiguous"
    # 无候选或所有候选均不兼容；引擎创建新 CanonicalEvent，不是事实已获确认。
    NEW_EVENT = "new_event"

@dataclass(frozen=True, slots=True)
class EventRecord:
    """一次带来源和存储身份的事件观察，是登记、查询和证据追溯的基本单位。

    多篇报道中的同一现实事件仍产生多条观察，不能复用同一 UUID 伪装成更新。
    语义字段平铺保留旧存储协议，通过 ir 提取纯语义；不是继承或嵌套 EventIR。
    登记前应完成全局实体解析；登记后保留原观察，由 CanonicalEvent 保存派生结果。
    """

    # 本条观察的全局唯一 UUID；Repository 主键，也是聚合成员和限定词来源引用。
    uuid: UUID
    # 来源情报/输入记录 UUID，用于追溯；内核不据此读取新闻正文或评估来源可信度。
    intelligence_uuid: UUID
    # 来源内部事件 ID，如 E1；不同来源可重复，不可取代 uuid 做全局身份。
    local_event_id: str
    # 与 EventIR.frame 相同的结构声明；通过 Registry 校验后登记。
    frame: Frame
    # 与 EventIR.predicate 相同；选择语义和身份规则，不以表述相似替代规范 ID。
    predicate: Predicate
    # 与 EventIR.role_bindings 相同；Engine 登记可按所选 role_groups 规范化语义组。
    role_bindings: tuple[RoleBinding, ...]
    # 与 EventIR.time 相同的事件时间表达；不是观察到达时间，状态/身份算法各有选取规则。
    time: Mapping[str, TimeExpression] = field(default_factory=dict)
    # 与 EventIR 中地点字段相同；用于位置查询、匹配和可选领域算法。
    location_entity_uuids: tuple[UUID, ...] = ()
    # 与 EventIR.attributes 相同；完整观察属性可多于 IdentitySpec 声明的身份属性。
    attributes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    # 与 EventIR.qualifiers 相同；需保留主张限定及其 by/time，不能只保存当前 phase。
    qualifiers: tuple[Qualifier, ...] = ()
    # 与 EventIR.relations 相同；关联观察，不代表 CanonicalEvent 的成员绑定。
    relations: tuple[EventRelation, ...] = ()
    # 系统观察/接收时间，建议带时区；用于查询排序和到达统计，不替代生命周期有效时间。
    observed_at: datetime | None = None
    # 是否为所属来源的主要事件，用于上层展示；当前核心匹配、查询不据此加权或筛选。
    is_primary: bool = False
    # 接入方附加元数据，如演示主题；不参与身份评分，不能替代正式语义字段。
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ir(self) -> EventIR:
        """提取不含来源、观察身份的 EventIR，供结构校验和纯语义分析使用。

        按 EventIR 的 dataclass 字段自动投影，保留规范字段。仅新建外层对象，不深拷贝
        time/attributes 等 Mapping；修改内部可变字典可能同时影响原观察，必须避免。
        """
        return EventIR(**{f.name: getattr(self, f.name) for f in fields(EventIR)})

    @classmethod
    def from_ir(cls, ir: EventIR, *, uuid: UUID, intelligence_uuid: UUID,
                local_event_id: str, observed_at: datetime | None = None,
                is_primary: bool = False, metadata: Mapping[str, Any] | None = None) -> EventRecord:
        """为纯语义补充观察身份，适用于抽取结果准备登记的阶段。

        ir 是语义输入；uuid 是本次观察主键，intelligence_uuid/local_event_id 用于来源
        追溯；observed_at 是到达时间，is_primary/metadata 是上层附加信息。所有身份
        参数均由调用方提供，本方法不生成 UUID、不解析局部实体、不校验 Registry，也
        不写存储。语义容器为浅共享，None 或空 metadata 会使用新的空字典。
        """
        return cls(uuid=uuid, intelligence_uuid=intelligence_uuid,
                   local_event_id=local_event_id, observed_at=observed_at,
                   is_primary=is_primary, metadata=metadata or {},
                   **{f.name: getattr(ir, f.name) for f in fields(EventIR)})

    def entities_for_role(self, role: str) -> tuple[UUID, ...]:
        """按领域角色名精确读取实体；与 EventIR 同名方法一致，缺失返回空元组。

        保留顺序和重复项，不读取配置，不将局部 ID 转成 UUID；适用于观察级身份要素读取。
        """
        return tuple(x.entity_uuid for x in self.role_bindings if x.role == role)

    def entities_for_group(self, group: SemanticRoleGroup) -> tuple[UUID, ...]:
        """按绑定中的通用语义组读取实体，适合观察级行动者/受作用者分析。

        不根据 role 动态查询 Registry；直接写存储绕过登记时，调用方需保证组映射正确。
        """
        return tuple(x.entity_uuid for x in self.role_bindings if x.semantic_group == group)

@dataclass(frozen=True, slots=True)
class CanonicalEvent:
    """同一现实事件的稳定身份及其成员观察的可重算物化投影。

    由 CanonicalEventMatcher 创建/重算，Repository 保存；不是原始事实或专题、战争、
    项目等系列容器。成员仍以原 EventRecord 保存，支持从观察恢复状态和来源。数据类
    本身不保证成员兼容，必须经身份规则、证据门槛和原成员冲突检查。
    """

    # 聚合身份的全局 UUID，与任何成员观察 UUID 区别；正常增加成员时保持稳定。
    uuid: UUID
    # 成员共用的规范谓词 ID；更新时必须一致，None 不意味着任意谓词可以混合。
    predicate_id: str | None
    # 初始事件的结构分类；新增成员须兼容配置，允许的变体不自动改变这一代表值。
    frame: Frame
    # 身份角色名→实体 UUID 集合的序列化表示；来自 IdentitySpec，不同取值不能直接取并集。
    identity_roles: Mapping[str, tuple[UUID, ...]]
    # 成员观察 UUID；用于追溯、重复解析检测和完整重算，不保存事件正文副本。
    observation_event_uuids: tuple[UUID, ...]
    # state_projection.values 的生命周期兼容视图；不含否认/预测等主张，不代表核实真值。
    current_qualifiers: Mapping[str, str]
    # 成员地点的聚合结果，更新时取去重并集；匹配不能只靠这个并集桥接不同原事件。
    location_entity_uuids: tuple[UUID, ...] = ()
    # 最早非空成员 observed_at，描述系统何时观察到，不是现实事件起点。
    first_observed_at: datetime | None = None
    # 最近非空成员 observed_at，描述到达历史；不直接决定当前生命周期。
    last_observed_at: datetime | None = None
    # 聚合事件时间的最早规范起点，保留日期/月份精度；用于身份候选比较，不是到达时间。
    event_time_start: str | None = None
    # 聚合事件时间的最晚规范终点；开放起点区间不虚构结束，不表示现实已完成。
    event_time_end: str | None = None
    # 当前重算的状态争议文本，与 state_projection.conflicts 联动；不是完整历史审计日志。
    unresolved_conflicts: tuple[str, ...] = ()
    # 物化版本，创建从 1 开始、成功重算递增；重复解析不递增，当前不提供并发 CAS 保证。
    version: int = 1
    # 区分角色快照，如卖方；用于已知不同则阻止合并，不给身份评分额外正分。
    discriminator_roles: Mapping[str, tuple[UUID, ...]] = field(default_factory=dict)
    # 仅声明的身份属性快照；普通金额等属性不自动进入，也不据展示文本比较。
    identity_attributes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    # 带来源、有效时间、支持观察和争议的完整状态投影；旧数据可能为 None，需从原成员重建。
    state_projection: StateProjection | None = None

@dataclass(frozen=True, slots=True)
class MatchResult:
    """可解释的候选匹配/解析结果，便于自动流程、人工复核和诊断。

    不能只看 score 决定合并；decision 已考虑必备证据、冲突、覆盖率及候选差值。
    文本原因当前是面向诊断的开放字符串，不应当稳定的机器错误码解析。
    """

    # 该次判断的动作类别；引擎实际重算后可能修正 STATE_UPDATE/SAME_EVENT 或改成歧义。
    decision: MatchDecision
    # 有效权重归一化的匹配分，通常在 [0,1]；不是事件发生概率或事实可信度。
    score: float
    # 对应候选聚合 UUID；歧义也可能指向最佳候选，None 常用于新建决策，不能据此断言已绑定。
    candidate_uuid: UUID | None = None
    # 已匹配证据的说明，如买方一致；供解释，不自动成为可核实事实。
    matched: tuple[str, ...] = ()
    # 缺失、精度不足、单位不可比较等未知原因；不等于明确冲突，可能单独阻止自动合并。
    unknown: tuple[str, ...] = ()
    # 不兼容原因或重算状态争议；有状态争议也可能已绑定，需结合 decision 判断。
    conflicts: tuple[str, ...] = ()
    # 可比较证据权重占比；不等于 score，角色部分重叠也可能可比较但未通过必备一致门槛。
    evidence_coverage: float = 0.0


@dataclass(frozen=True, slots=True)
class QualifierObservation:
    """限定词的来源外壳，由状态投影生成，适用于证据回溯和争议解释。

    它保留所有限定词，包括未进入生命周期值的否认、预测和其他作用域；不是另一条
    需要独立存储的 EventRecord，也不判断限定词所表达的主张是否真实。
    """

    # 此限定词所在的观察 UUID；结合 qualifier.id 定位，支持回到原始事件。
    event_uuid: UUID
    # 观察所属来源情报 UUID；用于来源追溯，不是 Qualifier.by 所指的主张主体。
    intelligence_uuid: UUID
    # 完整原限定词，包含作用域、by/time/surface；不要仅保留 type/value。
    qualifier: Qualifier
    # core.temporal 解析的有效时间下界；缺失/近似/无效为 None，不用 observed_at 代替。
    effective_at: datetime | None
    # 原观察的系统到达时间；供迟到报道和来源审计，不能冒充有效时间。
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class StateProjection:
    """一组同身份观察的报道状态投影，不承担事实真值裁决。

    EventAnalyzer.project_state 只保证输入谓词一致，不自行证明观察身份相同；用于
    CanonicalEvent 时应先由匹配器确定成员。按有效时间和 LifecycleSpec 重算，主张
    门控限制肯定生命周期；非法迁移、同时间不同值和不同主体分歧不会任意覆盖。
    """

    # 可投影的 phase/intention/authorization/directive 值；争议维度可能缺省，不是完整真值表。
    values: Mapping[str, str] = field(default_factory=dict)
    # 维度→支持当前值的观察 UUID；追溯依据，不表示全部成员或按可信度加权的证据。
    supporting_event_uuids: Mapping[str, tuple[UUID, ...]] = field(default_factory=dict)
    # 所有原限定词的观察外壳，含未采用项；用于保留否认、主体、有效时间及来源。
    observations: tuple[QualifierObservation, ...] = ()
    # 矛盾/非法迁移等争议；当前有任意此项时 assertion_status 为 disputed。
    conflicts: tuple[str, ...] = ()
    # 无有效时间、无迁移定义或未定义状态等无法判断项；只出现未知时仍可为 unverified。
    unknown: tuple[str, ...] = ()
    # unverified=未核实，disputed=存在争议；无 verified 输出，未争议不等于已证实。
    assertion_status: str = "unverified"

@dataclass(frozen=True, slots=True)
class EventRoleClassification:
    """基于 PredicateSpec.arguments 的主体、客体和其他角色展示投影。

    与 AGENT/AFFECTED 语义组不是一回事，例如转移主题也可以是展示主体。无已注册
    配置时按 Frame 使用有限兜底角色名，不推断真实语义；主体/客体配置可以重叠，
    当前不会强制三组互斥，应由配置维护者避免不必要重叠。
    """

    # 被 subject_roles 选中的完整绑定；可能多个或为空，不删除其原角色和实体信息。
    subjects: tuple[RoleBinding, ...] = ()
    # 被 object_roles 选中的完整绑定；不一定是 AFFECTED，也不代表负面影响。
    objects: tuple[RoleBinding, ...] = ()
    # 没有被主体/客体配置选中的剩余绑定，如工具、端点、地点；不是 OTHER 语义组的同义词。
    others: tuple[RoleBinding, ...] = ()
