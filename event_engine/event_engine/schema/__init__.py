"""事件引擎公共 schema 的统一导入入口，面向接入方、领域配置和存储适配器。

模块职责与使用时机：
    models：EventIR/Frame/角色等表达纯语义，EventRecord 包装来源观察，CanonicalEvent
        表达同一现实事件的稳定身份；MatchResult/StateProjection 是可解释派生结果。
    specs：声明 PredicateSpec、IdentitySpec、LifecycleSpec、DomainPack；具体实例在
        domains，注册、验证和推理算法在 core，而不是在这些声明中运行。
    queries：EventQuery/EventPage 表达存储无关的观察查询与分页，不等于事件时间线。
    ports：EventRepository/CanonicalEventRepository 定义存储最小接口，不实现数据库。

可直接 ``from event_engine.schema import EventRecord, IdentitySpec, EventQuery``，
也可从上述子模块精确导入；二者导出的类型对象相同，不存在额外包装或默认组装。
本入口不加载 core、domains、数据库适配器或 examples，适合只需要数据契约的外部系统。

使用限制：类型标注和 dataclass 不自动验证数据；frozen 不深度冻结字典；纯语义、
观察身份、现实事件身份、主张真值必须区分。每一类型及字段的详细限制和联动见定义。
扩展具体领域字段/输出时优先放 domains 的专用 schema，不把所有业务派生数据塞入公共模型。
"""
from .models import (
    Agency, CanonicalEvent, Dynamics, EventIR, EventRecord, EventRelation,
    EventRoleClassification, Frame, MatchDecision, MatchResult, Predicate,
    Qualifier, QualifierObservation, RoleBinding, SemanticRoleGroup,
    StateProjection, TimeExpression, Topology,
)
from .specs import ArgumentRoleSpec, DomainPack, IdentitySpec, LifecycleSpec, PredicateSpec
from .queries import EventPage, EventQuery
from .ports import CanonicalEventRepository, EventRepository

# 公共导出清单：限定星号导入和 API 发现范围；不注册谓词、不启用算法、不保证数据可直接序列化。
# 清单中的每一项沿用其定义模块的行为和约束，详细中文说明位于对应类、字段和方法。
__all__ = [
    "Agency", "CanonicalEvent", "Dynamics", "EventIR", "EventRecord", "EventRelation",
    "EventRoleClassification", "Frame", "MatchDecision", "MatchResult", "Predicate",
    "Qualifier", "QualifierObservation", "RoleBinding", "SemanticRoleGroup",
    "StateProjection", "TimeExpression", "Topology", "ArgumentRoleSpec", "DomainPack",
    "IdentitySpec", "LifecycleSpec", "PredicateSpec", "EventPage", "EventQuery",
    "CanonicalEventRepository", "EventRepository",
]
