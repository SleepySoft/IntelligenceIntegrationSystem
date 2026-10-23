"""领域语义规则的数据契约：定义规则形状，不执行匹配或状态推理。

具体实例放在 domains/<领域>/config.py，经 core.PredicateRegistry 检查和冻结后，
供接入、角色分析、身份匹配与状态投影共享。这里的 dataclass 本身不校验类型、
数值范围或角色引用；直接构造成功不代表配置有效。frozen 不深度冻结映射，注册前
调用方仍须避免修改内部字典。添加配置字段也不意味着内核自动支持相应行为。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Mapping

from .models import Frame, SemanticRoleGroup


@dataclass(frozen=True, slots=True)
class IdentitySpec:
    """某一谓词的现实事件身份规则，适用于比较观察与 CanonicalEvent。

    先检查已知冲突及必备证据，再按启用维度的权重归一化评分，并检查候选领先差。
    UNKNOWN 不给正分，但不是 CONFLICT；缺少必备证据时不能用其它维度的高分补偿。
    这里不配置事件真值或来源可信度，也不把生命周期变化当成身份证据。

    例：收购以买方、资产为身份角色，卖方可为区分角色；攻击还需要较精确的时间和
    关键地点，防止同一参与者的多次袭击被误合并。阈值是领域策略，不是概率校准。
    """

    # 用于身份评分的角色名；必须在 PredicateSpec.role_groups 声明，默认同时是合并必备角色。
    identity_roles: tuple[str, ...]
    # 只负责排除已知差异的角色，如 seller；双方非空而集合不同则冲突，缺失为未知，不额外补分。
    discriminator_roles: tuple[str, ...] = ()
    # 时间模式：occurrence=单次发生，episode=一段事件，interval=完整区间，ignore=不比较时间。
    # 当前 occurrence/episode 都比较起点精度范围；interval 才比较起止区间。occurrence/interval
    # 在 time_weight>0 时要求时间可比较，episode 不隐含此门槛，可另设 auto_merge_require_time。
    time_mode: str = "episode"
    # 不重叠时间的最大可容忍间隔，须为正；None 不表示无限容忍，不重叠且无窗口时返回未知。
    # occurrence/episode 比较起点距离，interval 比较区间间隙；重叠可得满时间分。
    time_tolerance: timedelta | None = None
    # strict=关键地点，不相交为冲突、缺失禁止合并；weak=不相交只得零分；ignore=不比较。
    # strict 在 location_weight=0 时仍执行约束；比较实体 UUID 集合，不做地理邻近或行政层级推导。
    location_mode: str = "weak"
    # 重复发生倾向的领域说明，如 high/medium/low；当前不自动改变窗口、阈值或候选召回。
    repeatability: str = "medium"
    # 身份角色维度权重；角色集合以交并比评分、缺失为零；必备一致性门槛不受此权重抵消。
    role_weight: float = 0.50
    # 时间维度权重；ignore 时不启用，零权重仍可通过 auto_merge_require_time 要求必备时间。
    time_weight: float = 0.20
    # 地点维度权重；按地点集合交并比评分，ignore 时不启用，不表示地理距离权重。
    location_weight: float = 0.15
    # 身份属性维度权重；只有声明的属性键参与，未声明任何身份属性时不产生该维度正分。
    attribute_weight: float = 0.10
    # 旧配置兼容字段；Registry 仍检查非负/有限，但不参与身份分或覆盖率，勿用于提高合并概率。
    lifecycle_weight: float = 0.05
    # 自动绑定的分数下限，[0,1]；还需证据门槛、无冲突和候选领先差，不是单一合并开关。
    auto_merge_threshold: float = 0.85
    # 已有足够证据时的复核分数下限；低于它可判该候选不同，证据不足仍是 ambiguous。
    # Registry 要求 0<=review_threshold<=auto_merge_threshold<=1。
    review_threshold: float = 0.65
    # 最佳与次佳可行候选的最低分差，[0,1]；不足则歧义，不是最佳分相对阈值的差。
    auto_merge_margin: float = 0.15
    # 参与身份比较的属性名，如 currency/maturity；候选快照存到 CanonicalEvent.identity_attributes。
    # 比较 value/type/currency/unit；不解析原文、不换算单位。未声明的金额变化不决定身份。
    identity_attributes: tuple[str, ...] = ()
    # 自动合并必备角色：None=全部 identity_roles，()=无额外必备子集；必须是身份角色子集。
    # 必备角色双方实体集合须齐全且完全一致，部分重叠也不通过；其他分数门槛仍然有效。
    auto_merge_required_roles: tuple[str, ...] | None = None
    # 自动合并必备属性键；缺失或不可比较禁止合并，与 identity_attributes 取并集执行比较。
    auto_merge_required_attributes: tuple[str, ...] = ()
    # 最低可比较证据权重占比，[0,1]；与相似度分开衡量，不能代替必备角色/时间检查。
    # 角色集合部分重叠可以算已比较证据，但仍可能因必备集合不一致而被拒绝自动合并。
    min_evidence_coverage: float = 0.75
    # 起点时间精度范围的最大宽度，须为正；例如一天可排除只有月精度的攻击观察。
    # 当前检查两侧起点范围，不全面检查 interval 的终点精度；None 表示不加此额外限制。
    max_time_uncertainty: timedelta | None = None
    # 是否独立要求可比较事件时间，即使 time_weight 为零；不能与 time_mode=ignore 同时配置。
    auto_merge_require_time: bool = False

    # 权重整体限制：Registry 要求有限、非负，且至少有启用的身份证据权重；不要求加和为 1，
    # 匹配器自行归一化。identity_roles 为空时当前不能自动合并，属性/时间不能单独替代角色。


@dataclass(frozen=True, slots=True)
class LifecycleSpec:
    """生命周期各维度的允许迁移图，供 core.state 在身份确定后重算报道状态。

    只配置 phase/intention/authorization/directive，不把 epistemic/modality/polarity
    放进状态迁移图。可达性算法在 core：允许跳过未报道的中间步骤，不生成中间事实；
    同值可持续，非法迁移记录争议。有效时间未知时不能确认不同值之间的迁移。
    图可以含回路，如 paused→ongoing，不存在统一的“后值总比前值高级”排序。
    """

    # 维度→允许的有向边集合，如 {"phase": frozenset({("ongoing", "completed")})}。
    # 某维度出现时，它的合法状态词取边的全部端点；空边集合不声明任何合法词。
    # 未配置维度可保留首次报告值，但不同值间迁移为未知；单状态须用自环显式声明。
    # Registry 冻结外层映射与边集合，但当前不全面校验每条边的形状/词值，配置方须保证二元字符串对。
    transitions: Mapping[str, frozenset[tuple[str, str]]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArgumentRoleSpec:
    """把谓词角色投影为展示主体和客体，供 EventAnalyzer.classify_roles 使用。

    主体不等于 AGENT，客体不等于 AFFECTED；它不改变实体身份，也不丢弃其原始角色。
    配置中未被两组选择的绑定归入 others。Registry 检查所用角色已声明，但目前不禁止
    两组重叠；若希望展示互斥，配置维护者需保证两组互斥。
    """

    # 用作逻辑/展示主体的角色名集合，允许多个或为空；空集合不从语义组自动补主体。
    subject_roles: frozenset[str] = frozenset()
    # 用作逻辑/展示客体的角色名集合，允许多个或为空；不推断受害、收益或语法宾语。
    object_roles: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class PredicateSpec:
    """单个规范谓词如何使用公共 schema 的完整规则声明。

    登记时使用必填角色、Frame 和角色语义组；分析使用展示投影；匹配使用 identity；
    生命周期重算使用 lifecycle。所有环节应共享同一 Registry，而不是各自加载默认规则。
    当前不是完整类型系统：没有实体类型约束、属性 schema、角色最大基数或开放字符串枚举校验。
    """

    # 稳定规范谓词 ID；必须与 DomainPack.specs/Registry 的映射键一致，不按标签推断同义 ID。
    predicate_id: str
    # 领域角色名→通用 SemanticRoleGroup；Engine 登记规范化绑定，Registry 校验被引用角色已声明。
    # 声明某角色不等于要求必填；未映射的额外角色目前不一概拒绝，也不自动推断其语义。
    role_groups: Mapping[str, SemanticRoleGroup]
    # 该谓词的现实事件身份规则；与 CanonicalEvent 的角色/属性快照及 MatchResult 联动。
    identity: IdentitySpec
    # 主体/客体展示投影；默认两组为空，已注册谓词不会因此自动套用未知谓词的 Frame 兜底。
    arguments: ArgumentRoleSpec = field(default_factory=ArgumentRoleSpec)
    # 领域/用途标签，如 war；供调用方或领域算法选取，不按标签自动加载配置或参与身份评分。
    tags: frozenset[str] = frozenset()
    # 接入校验必填角色；缺失可直接拒绝登记，与“允许保存但不能自动合并”的必备身份角色不同。
    required_roles: tuple[str, ...] = ()
    # 默认允许的结构；None 且 allowed_frames 为空时不施加配置 Frame 约束，但匹配仍比较双方结构。
    frame: Frame | None = None
    # 显式允许的结构变体；非空时优先于 frame，两个已获允许的变体可以进行身份比较。
    # dynamics/topology 须匹配某允许项；agency 的 UNKNOWN 与已知项兼容。
    allowed_frames: tuple[Frame, ...] = ()
    # 状态迁移图；None 不表示任意不同状态可覆盖，仅保留首次值/同值，缺规则的迁移记未知。
    lifecycle: LifecycleSpec | None = None
    # 面向抽取器和人工展示的稳定名称与边界说明。它们属于领域词表，不能由 Prompt
    # 另行维护；核心匹配算法不读取这两个字段。
    label: str = ""
    definition: str = ""


@dataclass(frozen=True, slots=True)
class DomainPack:
    """一个可选领域的谓词声明集合，与领域专用算法分开加载。

    domains 提供具体配置，PredicateRegistry.from_packs 组合、检查重名并冻结。
    核心只消费 specs，不按 domain_id 分支；同一事件可使用来自多个领域包的词汇。
    本类型不包含数据库、加载函数或分析算法，也不在构造时自动冻结内部字典。
    """

    # 包的维护标识，如 news/industry/financial；组合时不允许重复同名包，不是事件领域标签。
    domain_id: str
    # 配置版本文本，如 1.1；Registry.pack_versions 留存，当前不自动做版本迁移或规则兼容判断。
    version: str
    # 规范谓词 ID→PredicateSpec；键须等于 spec.predicate_id，组合包不能重复定义同一谓词。
    # 不把实体类型表或领域结果模型塞入这里；专用声明留在对应 domains 目录。
    specs: Mapping[str, PredicateSpec]
