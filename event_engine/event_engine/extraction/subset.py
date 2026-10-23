"""面向 LLM 的事件抽取核心子集契约与确定性桥接。

设计原则：LLM 只输出需要语义判断的最小结构——谓词、角色与内联实体提及、
限定词、事件关系、时间与属性。一切机械性内容（schema_version、ENT/E/Q 局部 ID、
引用完整性、Frame 查表、主事件引用、限定词编号、时间格式修复）由
:func:`complete_core_extraction` 确定性补齐，产出与 ``event-extraction/1.0``
完全一致的 :class:`EventExtractionResult`，继续走原有的结构加 Registry 语义校验。

相对完整契约，子集在 LLM 接口上放弃两项能力（存储契约不受影响）：

- 限定词不支持 ``scope`` 嵌套（一律视为 event 级）；
- 同名但不同指的实体依赖名称消歧（如“张伟（国防部长）”），因为 name 是合并键。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import (
    EntityType,
    EventExtractionResult,
    ExtractedAttributes,
    ExtractedFrame,
    ExtractedPredicate,
    QualifierType,
    StrictModel,
    validate_event_extraction,
)

_SUBSET_SCHEMA_NOTE = "core-subset 不声明独立存储版本；桥接产物仍是 event-extraction/1.0"


class SubsetEntity(StrictModel):
    """内联实体提及；name 是跨角色/事件合并的确定性键。"""

    name: str = Field(..., min_length=1)
    type: EntityType
    country_code: str | None = Field(None, pattern=r"^[A-Z]{2}$")


class SubsetTimeExpression(StrictModel):
    """AI 只需给规范值和原文；其余字段由桥接层确定性生成。

    precision/approximate 暂时接收旧 Prompt 的输出以兼容在途任务，但桥接时不采信。
    """

    normalized: str | None = None
    surface: str = Field(..., min_length=1)
    precision: Literal["year", "month", "day", "hour", "minute"] | None = None
    approximate: bool | None = None


class SubsetTime(StrictModel):
    event_time: SubsetTimeExpression | None = None
    start_time: SubsetTimeExpression | None = None
    end_time: SubsetTimeExpression | None = None
    effective_time: SubsetTimeExpression | None = None
    deadline: SubsetTimeExpression | None = None
    expected_start_time: SubsetTimeExpression | None = None
    expected_end_time: SubsetTimeExpression | None = None

    @model_validator(mode="after")
    def check_time_fields(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("time must contain at least one field")
        if self.event_time is not None and (self.start_time is not None or self.end_time is not None):
            raise ValueError("event_time cannot coexist with start_time or end_time")
        return self


class SubsetContext(StrictModel):
    event_location: list[SubsetEntity] = Field(..., min_length=1)


class SubsetQualifier(StrictModel):
    """无 id/scope 的限定词；scope 一律为 event。"""

    type: QualifierType
    value: str = Field(..., min_length=1)
    by: list[SubsetEntity] | None = None
    time: SubsetTimeExpression | None = None
    surface: str | None = None

    @model_validator(mode="after")
    def check_actor_rules(self):
        if self.type == "epistemic" and not self.by:
            raise ValueError("by is required for epistemic qualifier")
        if self.type in {"phase", "polarity"} and self.by:
            raise ValueError(f"by is not allowed for qualifier type '{self.type}'")
        return self


RelationPredicate = Literal[
    "causes", "promotes", "prevents", "aggravates", "mitigates",
    "precedes", "follows", "overlaps", "condition_for", "part_of",
]


class SubsetRelation(StrictModel):
    """以 events 数组的 1 起始位置序号引用目标事件，替代局部事件 ID。"""

    predicate: RelationPredicate
    target: int = Field(..., ge=1)
    surface: str | None = None


class SubsetEvent(StrictModel):
    """扁平事件：完整契约中 core 的字段直接平铺，减少一层嵌套。"""

    is_primary: bool = False
    predicate: ExtractedPredicate
    frame: ExtractedFrame | None = None
    agency: Literal["agentive", "non_agentive", "unknown"] | None = None
    roles: dict[str, list[SubsetEntity]] = Field(..., min_length=1)
    time: SubsetTime | None = None
    context: SubsetContext | None = None
    attributes: ExtractedAttributes | None = None
    qualifiers: list[SubsetQualifier] = Field(default_factory=list, max_length=8)
    relations: list[SubsetRelation] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def check_roles(self):
        if any(not role or not references for role, references in self.roles.items()):
            raise ValueError("each role must be named and reference at least one entity")
        return self


class CoreExtractionSubset(StrictModel):
    """LLM 面向的核心子集：只有一个 events 数组。"""

    events: list[SubsetEvent] = Field(..., min_length=1, max_length=3)


_PRECISION_PATTERNS = {
    "year": r"^\d{4}$",
    "month": r"^\d{4}-\d{2}$",
    "day": r"^\d{4}-\d{2}-\d{2}$",
    "hour": r"^\d{4}-\d{2}-\d{2}T\d{2}$",
    "minute": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$",
}

_PRECISION_STRPTIME = {
    "year": "%Y",
    "month": "%Y-%m",
    "day": "%Y-%m-%d",
    "hour": "%Y-%m-%dT%H",
    "minute": "%Y-%m-%dT%H:%M",
}

# 允许秒与更小粒度、时区后缀、空格分隔等常见偏差，统一截断/剔除后再按精度校验。
_LOOSE_TIME_PATTERN = re.compile(
    r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?(?:T(\d{2})(?::(\d{2}))?(?::\d{2}(?:\.\d+)?)?)?"
)

# 各精度从年到该精度所需的分量数量。
_PRECISION_DEPTH = {"year": 1, "month": 2, "day": 3, "hour": 4, "minute": 5}


def _valid_calendar(text: str, precision: str) -> bool:
    try:
        datetime.strptime(text, _PRECISION_STRPTIME[precision])
    except ValueError:
        return False
    return True


def normalize_time_text(value: str, precision: str) -> str | None:
    """尽力把 LLM 的时间文本修复为与 precision 严格对应的格式。

    修复手段：空白转 T、剥时区、截断秒及更低精度。无法修复或日历上
    不存在时返回 None（契约允许 normalized 为 null），避免整次输出重试。
    """
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"[Tt\s]+", "T", text)
    text = re.sub(r"(?:[Zz]|[+-]\d{2}:?\d{2})$", "", text)
    if re.fullmatch(_PRECISION_PATTERNS[precision], text):
        return text if _valid_calendar(text, precision) else None
    match = _LOOSE_TIME_PATTERN.fullmatch(text)
    if not match:
        return None
    components = list(match.groups())
    depth = _PRECISION_DEPTH[precision]
    if any(component is None for component in components[:depth]):
        # 声明精度高于文本实际精度（如 precision=day 但只有年月），不可编造。
        return None
    # 截断到声明精度，丢弃更细的分量。
    rebuilt = components[0]
    for component, separator in zip(components[1:depth], ("-", "-", "T", ":")):
        rebuilt += separator + component
    if not re.fullmatch(_PRECISION_PATTERNS[precision], rebuilt):
        return None
    return rebuilt if _valid_calendar(rebuilt, precision) else None


def infer_time_precision(value: str | None) -> str | None:
    """从 ISO 风格规范值确定性推导精度；不可解析时返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"[Tt\s]+", "T", text)
    text = re.sub(r"(?:[Zz]|[+-]\d{2}:?\d{2})$", "", text)
    match = _LOOSE_TIME_PATTERN.fullmatch(text)
    if not match:
        return None
    year, month, day, hour, minute = match.groups()
    if minute is not None:
        precision = "minute"
    elif hour is not None:
        precision = "hour"
    elif day is not None:
        precision = "day"
    elif month is not None:
        precision = "month"
    elif year is not None:
        precision = "year"
    else:
        return None
    return precision if normalize_time_text(text, precision) is not None else None


def _repair_time_expression(expr: SubsetTimeExpression) -> None:
    # 不采信 AI 对 precision/approximate 的判断。precision 可由值的形状唯一推导；
    # approximate 无法可靠机械判断，统一保留为 unknown(None)。
    expr.precision = infer_time_precision(expr.normalized)
    expr.normalized = (
        normalize_time_text(expr.normalized, expr.precision)
        if expr.normalized and expr.precision else None
    )
    expr.approximate = None


_TIME_SLOTS = (
    "event_time", "start_time", "end_time", "effective_time",
    "deadline", "expected_start_time", "expected_end_time",
)


def _repair_event_times(event: SubsetEvent) -> None:
    if event.time is not None:
        for slot in _TIME_SLOTS:
            expression = getattr(event.time, slot)
            if expression is not None:
                _repair_time_expression(expression)
    for qualifier in event.qualifiers:
        if qualifier.time is not None:
            _repair_time_expression(qualifier.time)


class _EntityTable:
    """按规范化名称合并内联实体提及，并分配 ENT 局部 ID。"""

    def __init__(self):
        self._ids_by_key: dict[str, str] = {}
        self._entities_by_key: dict[str, dict[str, Any]] = {}
        self._ordered_keys: list[str] = []

    @staticmethod
    def _key(name: str) -> str:
        return re.sub(r"\s+", "", name).casefold()

    def intern(self, ref: SubsetEntity) -> str:
        key = self._key(ref.name)
        entity_id = self._ids_by_key.get(key)
        if entity_id is not None:
            entity = self._entities_by_key[key]
            # 后出现的提及只允许补全此前缺失的 country_code，其余以首次提及为准。
            if entity["country_code"] is None and ref.country_code:
                entity["country_code"] = ref.country_code
            return entity_id
        entity_id = f"ENT{len(self._ordered_keys) + 1}"
        entity = {
            "id": entity_id,
            "name": ref.name.strip(),
            "type": ref.type,
            "country_code": ref.country_code,
        }
        self._ids_by_key[key] = entity_id
        self._entities_by_key[key] = entity
        self._ordered_keys.append(key)
        return entity_id

    def entities(self) -> list[dict[str, Any]]:
        return [self._entities_by_key[key] for key in self._ordered_keys]


def _derive_frame(event: SubsetEvent, registry) -> dict[str, str]:
    """已登记谓词的 Frame 由 Registry 查表确定；开放谓词要求 LLM 提供。"""
    predicate_id = event.predicate.id
    if predicate_id is None:
        if event.frame is None:
            raise ValueError("predicate.id 为 null 的开放谓词必须提供 frame")
        return {
            "dynamics": event.frame.dynamics,
            "topology": event.frame.topology,
            "agency": event.frame.agency,
        }
    spec = registry.get(predicate_id)
    if spec is None:
        raise ValueError(f"unregistered predicate: {predicate_id}")
    if event.frame is not None:
        raise ValueError(f"谓词 {predicate_id} 的 frame 由 Registry 决定，子集输出不得提供")
    frames = spec.allowed_frames or ((spec.frame,) if spec.frame is not None else ())
    if not frames:
        raise ValueError(f"谓词 {predicate_id} 未登记可用 Frame")
    frame = frames[0]
    agency = frame.agency.value
    if agency == "unknown" and event.agency:
        # 仅对 Registry 标注 agency=unknown 的谓词采纳 LLM 提示，其余以 Registry 为准。
        agency = event.agency
    return {
        "dynamics": frame.dynamics.value,
        "topology": frame.topology.value,
        "agency": agency,
    }


def complete_core_extraction(data: Any, registry) -> EventExtractionResult:
    """把核心子集确定性补齐为完整 event-extraction/1.0 结果并校验。"""
    subset = (
        data if isinstance(data, CoreExtractionSubset)
        else CoreExtractionSubset.model_validate(data)
    )
    primaries = [index for index, event in enumerate(subset.events) if event.is_primary]
    if len(primaries) > 1:
        raise ValueError("is_primary 只能标记一项事件")
    primary_index = primaries[0] if primaries else 0

    table = _EntityTable()
    events_payload: list[dict[str, Any]] = []
    for index, event in enumerate(subset.events):
        event_id = f"E{index + 1}"
        _repair_event_times(event)
        roles = {
            role: [table.intern(ref) for ref in refs]
            for role, refs in event.roles.items()
        }
        core: dict[str, Any] = {
            "frame": _derive_frame(event, registry),
            "predicate": event.predicate.model_dump(mode="python"),
            "roles": roles,
        }
        if event.time is not None:
            # 完整契约的 normalized 是必现可空键，此处不排除 None。
            core["time"] = event.time.model_dump(mode="python")
        if event.context is not None:
            core["context"] = {
                "event_location": [table.intern(ref) for ref in event.context.event_location]
            }
        if event.attributes is not None:
            core["attributes"] = event.attributes.model_dump(mode="python", exclude_none=True)

        qualifiers: list[dict[str, Any]] = []
        for qualifier_index, qualifier in enumerate(event.qualifiers):
            item: dict[str, Any] = {
                "id": f"Q{qualifier_index + 1}",
                "type": qualifier.type,
                "value": qualifier.value,
                "scope": "event",
            }
            if qualifier.by:
                item["by"] = [table.intern(ref) for ref in qualifier.by]
            if qualifier.time is not None:
                item["time"] = qualifier.time.model_dump(mode="python")
            if qualifier.surface:
                item["surface"] = qualifier.surface
            qualifiers.append(item)

        relations: list[dict[str, Any]] = []
        for relation in event.relations:
            if relation.target > len(subset.events):
                raise ValueError(
                    f"event {event_id} relation target {relation.target} "
                    f"exceeds events count {len(subset.events)}"
                )
            target_id = f"E{relation.target}"
            if target_id == event_id:
                raise ValueError(f"event {event_id} cannot relate to itself")
            item = {"predicate": relation.predicate, "target_event_id": target_id}
            if relation.surface:
                item["surface"] = relation.surface
            relations.append(item)

        events_payload.append({
            "id": event_id,
            "core": core,
            "qualifiers": qualifiers,
            "relations": relations,
        })

    payload = {
        "schema_version": "event-extraction/1.0",
        "primary_event_id": f"E{primary_index + 1}",
        "entities": table.entities(),
        "events": events_payload,
    }
    return validate_event_extraction(payload, registry)
