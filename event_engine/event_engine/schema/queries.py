"""存储无关的事件查询和分页结果协议。

EventQuery 只表达条件，core.EventEngine.query 将其交给 Repository；内存与 MongoDB
适配器负责执行。它查询 EventRecord 观察，不查询 CanonicalEvent 或事件真值。类型
标注不会自动验证页码、时间或集合元素，调用方应先校验边界和时区。
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet
from uuid import UUID
from .models import SemanticRoleGroup

@dataclass(frozen=True, slots=True)
class EventQuery:
    """观察级筛选请求，适合实体行为检索、来源追溯和登记数据查询。

    不同启用条件总体按 AND 组合，同一集合内按“任意一个命中”处理；空集合/None
    通常表示不限制，而不是要求数据库字段为空。角色条件只在 entity_uuid 指定时
    启用，且必须由同一条角色绑定同时满足。限定词的 type/value 则是独立存在性
    条件，当前不要求同一条限定词同时满足，不能把它当成严格的键值对查询。

    当前没有关系遍历、属性条件、事件有效时间条件、主张主体条件或排序参数。
    适配器通常按 observed_at/UUID 排序，与 EventAnalyzer.timeline 的事件时间排序不同。
    """

    # 允许的观察 UUID 集合；空集合不限制，不能用空集合表达“明确返回零条”。
    event_uuids: FrozenSet[UUID] = frozenset()
    # 精确匹配来源情报 UUID，用于查同一输入产生的观察；None 不限制，不会读取来源正文。
    intelligence_uuid: UUID | None = None
    # 规范谓词 ID 集合，任一命中；不解析 surface，也不展开 domains 标签，无法专门筛选 None ID。
    predicate_ids: FrozenSet[str] = frozenset()
    # 指定参与实体 UUID，要求至少一条角色绑定命中；不是地点集合或 Qualifier.by 的主体查询。
    entity_uuid: UUID | None = None
    # entity_uuid 在事件中允许扮演的领域角色名；为空不限制，无 entity_uuid 时目前忽略。
    # 同一绑定还须满足启用的 semantic_groups，不能分别在两条绑定上命中。
    roles: FrozenSet[str] = frozenset()
    # entity_uuid 允许的通用语义组；为空不限制，无 entity_uuid 时目前忽略。
    # 查询使用已存绑定的组，不实时重算 Registry；需在登记时保证映射正确。
    semantic_groups: FrozenSet[SemanticRoleGroup] = frozenset()
    # 事件地点 UUID 的任一交集命中；不查询 LOCATION 角色，不进行地理邻近/包含关系推导。
    location_entity_uuids: FrozenSet[UUID] = frozenset()
    # 到达/观察时间的含等号下界；不是发生时间，建议带时区，与 observed_to 共同形成闭区间。
    observed_from: datetime | None = None
    # 到达/观察时间的含等号上界；启用边界时缺 observed_at 的观察不匹配，调用方校验上下界顺序。
    observed_to: datetime | None = None
    # 至少存在一种所选限定词 type；不限制其 scope/by，也不要求与 qualifier_values 同条命中。
    qualifier_types: FrozenSet[str] = frozenset()
    # 至少存在一种所选限定词 value；这是原始观察限定词，不是 CanonicalEvent 当前生命周期。
    qualifier_values: FrozenSet[str] = frozenset()
    # 返回条数上限；None 表示不限，正常分页应传正整数。本模型不拒绝零/负数：
    # limit=0 在内存切片中为空，但 MongoDB limit(0) 表示不限，故跨适配器不要使用零。
    limit: int | None = None
    # 跳过的匹配条数，不是页码；应非负，页码需由调用方转换，负数在不同适配器行为不一致。
    offset: int = 0

@dataclass(frozen=True, slots=True)
class EventPage:
    """Repository.search 的分页快照，适合 API 返回和分页 UI。

    total 是应用分页前的匹配总数，items 是此页观察。这里没有类型/数量校验，
    items 标注为宽泛 tuple，事件 Repository 约定装 EventRecord。没有游标、下一页
    Token 或稳定快照保证，并发增删时 total 和 items 是否一致取决于具体存储实现。
    """

    # 本页条目，空元组可能表示无匹配或 offset 越界；不能据长度推断整个查询总数。
    items: tuple
    # 未分页匹配条数；不是 len(items)，应由 Repository 在 offset/limit 之前统计。
    total: int
    # 本页实际采用的跳过条数，回显 EventQuery.offset；不等于当前页号。
    offset: int = 0
    # 本页采用的上限，回显 EventQuery.limit；None 不限，不保证 items 恰好达到此数量。
    limit: int | None = None
