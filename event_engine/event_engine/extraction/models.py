"""LLM 事件抽取的数据契约。

这里仅描述来源内的局部实体和局部事件。转换为全局实体 UUID 和 ``EventRecord`` 属于
接入用例层。谓词、Frame、角色与生命周期词值通过调用方传入的 Registry 校验。
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..schema import Agency


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


EntityType = Literal[
    "person", "organization", "geopolitical_entity", "location", "facility",
    "equipment", "product", "resource", "asset", "information", "policy",
    "agreement", "position", "capability", "topic", "phenomenon", "case",
    "event", "other_object",
]


class ExtractedEntity(StrictModel):
    id: str = Field(..., pattern=r"^ENT[1-9][0-9]*$")
    name: str = Field(..., min_length=1)
    type: EntityType
    country_code: str | None = Field(None, pattern=r"^[A-Z]{2}$")


class ExtractedFrame(StrictModel):
    dynamics: Literal["state", "process", "change"]
    topology: Literal["intrinsic", "relational", "targeted", "transfer"]
    agency: Literal["agentive", "non_agentive", "unknown"]


class ExtractedPredicate(StrictModel):
    id: str | None
    surface: str = Field(..., min_length=1)
    gloss: str | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def check_gloss(self):
        if self.id is None and not self.gloss:
            raise ValueError("gloss is required when predicate id is null")
        if self.id is not None and self.gloss is not None:
            raise ValueError("gloss is only allowed when predicate id is null")
        return self


class ExtractedTimeExpression(StrictModel):
    normalized: str | None
    precision: Literal["year", "month", "day", "hour", "minute"]
    approximate: bool
    surface: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def check_normalized_precision(self):
        if self.normalized is None:
            return self
        import re
        patterns = {
            "year": r"^\d{4}$",
            "month": r"^\d{4}-\d{2}$",
            "day": r"^\d{4}-\d{2}-\d{2}$",
            "hour": r"^\d{4}-\d{2}-\d{2}T\d{2}$",
            "minute": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$",
        }
        if not re.fullmatch(patterns[self.precision], self.normalized):
            raise ValueError("normalized time does not match precision")
        return self


class ExtractedTime(StrictModel):
    event_time: ExtractedTimeExpression | None = None
    start_time: ExtractedTimeExpression | None = None
    end_time: ExtractedTimeExpression | None = None
    effective_time: ExtractedTimeExpression | None = None
    deadline: ExtractedTimeExpression | None = None
    expected_start_time: ExtractedTimeExpression | None = None
    expected_end_time: ExtractedTimeExpression | None = None

    @model_validator(mode="after")
    def check_time_fields(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("time must contain at least one field")
        if self.event_time is not None and (self.start_time is not None or self.end_time is not None):
            raise ValueError("event_time cannot coexist with start_time or end_time")
        return self


class NumberAttribute(StrictModel):
    type: Literal["number"]
    value: float
    unit: str = Field(..., min_length=1)
    surface: str | None = None


class MoneyAttribute(StrictModel):
    type: Literal["money"]
    value: float
    currency: str = Field(..., pattern=r"^[A-Z]{3}$")
    unit: str | None = None
    surface: str | None = None


class RatioAttribute(StrictModel):
    type: Literal["ratio"]
    value: float = Field(..., ge=0, le=1)
    surface: str | None = None


class DurationAttribute(StrictModel):
    type: Literal["duration"]
    value: float
    unit: str = Field(..., min_length=1)
    surface: str | None = None


class ExtractedAttributes(StrictModel):
    amount: MoneyAttribute | None = None
    quantity: NumberAttribute | None = None
    ratio: RatioAttribute | None = None
    value_before: NumberAttribute | None = None
    value_after: NumberAttribute | None = None
    delta: NumberAttribute | None = None
    duration: DurationAttribute | None = None
    level: NumberAttribute | None = None

    @model_validator(mode="after")
    def require_attribute(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("attributes must contain at least one field")
        return self


class ExtractedContext(StrictModel):
    event_location: list[str] = Field(..., min_length=1)


QualifierType = Literal[
    "phase", "intention", "authorization", "directive",
    "epistemic", "modality", "polarity",
]

NON_LIFECYCLE_QUALIFIER_VALUES: Mapping[str, frozenset[str]] = {
    "epistemic": frozenset({"asserted", "estimated", "doubted", "denied"}),
    "modality": frozenset({"possible", "probable", "conditional"}),
    "polarity": frozenset({"negated"}),
}


class ExtractedQualifier(StrictModel):
    id: str = Field(..., pattern=r"^Q[1-9][0-9]*$")
    type: QualifierType
    value: str = Field(..., min_length=1)
    by: list[str] | None = None
    scope: str = Field(..., pattern=r"^(event|Q[1-9][0-9]*)$")
    time: ExtractedTimeExpression | None = None
    surface: str | None = None

    @model_validator(mode="after")
    def check_actor_rules(self):
        if self.type == "epistemic" and not self.by:
            raise ValueError("by is required for epistemic qualifier")
        if self.type in {"phase", "polarity"} and self.by:
            raise ValueError(f"by is not allowed for qualifier type '{self.type}'")
        return self


class ExtractedRelation(StrictModel):
    predicate: Literal[
        "causes", "promotes", "prevents", "aggravates", "mitigates",
        "precedes", "follows", "overlaps", "condition_for", "part_of",
    ]
    target_event_id: str = Field(..., pattern=r"^E[1-9][0-9]*$")
    surface: str | None = None


class ExtractedEventCore(StrictModel):
    frame: ExtractedFrame
    predicate: ExtractedPredicate
    roles: dict[str, list[str]] = Field(..., min_length=1)
    time: ExtractedTime | None = None
    context: ExtractedContext | None = None
    attributes: ExtractedAttributes | None = None

    @model_validator(mode="after")
    def check_roles(self):
        if any(not role or not references for role, references in self.roles.items()):
            raise ValueError("each role must be named and reference at least one entity")
        return self


class ExtractedEvent(StrictModel):
    id: str = Field(..., pattern=r"^E[1-9][0-9]*$")
    core: ExtractedEventCore
    qualifiers: list[ExtractedQualifier] = Field(default_factory=list, max_length=8)
    relations: list[ExtractedRelation] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def check_qualifier_scopes(self):
        scopes: dict[str, str] = {}
        for qualifier in self.qualifiers:
            if qualifier.id in scopes:
                raise ValueError(f"duplicate qualifier id: {qualifier.id}")
            if qualifier.scope != "event":
                if qualifier.scope not in scopes:
                    raise ValueError(f"qualifier scope must reference an earlier qualifier: {qualifier.scope}")
                if scopes[qualifier.scope] != "event":
                    raise ValueError("qualifier nesting cannot exceed one level")
            scopes[qualifier.id] = qualifier.scope
        return self


class EventExtractionResult(StrictModel):
    schema_version: Literal["event-extraction/1.0"]
    primary_event_id: str = Field(..., pattern=r"^E[1-9][0-9]*$")
    entities: list[ExtractedEntity] = Field(..., max_length=64)
    events: list[ExtractedEvent] = Field(..., min_length=1, max_length=3)

    @model_validator(mode="after")
    def check_references(self):
        entity_ids = [entity.id for entity in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("entities contains duplicate IDs")
        entity_id_set = set(entity_ids)

        event_ids = [event.id for event in self.events]
        event_id_set = set(event_ids)
        if len(event_ids) != len(event_id_set):
            raise ValueError("events contains duplicate IDs")
        if self.primary_event_id not in event_id_set:
            raise ValueError("primary_event_id must reference events")

        for event in self.events:
            references = [ref for refs in event.core.roles.values() for ref in refs]
            if event.core.context:
                references.extend(event.core.context.event_location)
            for qualifier in event.qualifiers:
                references.extend(qualifier.by or [])
            missing = sorted(set(references) - entity_id_set)
            if missing:
                raise ValueError(f"event {event.id} references unknown entities: {missing}")
            for relation in event.relations:
                if relation.target_event_id == event.id:
                    raise ValueError(f"event {event.id} cannot relate to itself")
                if relation.target_event_id not in event_id_set:
                    raise ValueError(f"event {event.id} references unknown event")
        return self


FALLBACK_ROLES = {
    "intrinsic": ({"subject"}, {"subject"}),
    "relational": ({"subject", "counterpart"}, {"subject", "counterpart"}),
    "targeted": ({"actor", "target"}, {"actor", "target", "instrument"}),
    "transfer": ({"theme"}, {"theme", "source", "destination", "agent"}),
}


def _frame_tuple(frame) -> tuple[str, str, str]:
    return frame.dynamics.value, frame.topology.value, frame.agency.value


def _validate_event_semantics(event: ExtractedEvent, registry) -> None:
    core = event.core
    predicate_id = core.predicate.id
    if predicate_id is None:
        required, allowed = FALLBACK_ROLES[core.frame.topology]
        if not required.issubset(core.roles) or not set(core.roles).issubset(allowed):
            raise ValueError(f"event {event.id} has invalid fallback roles")
        if core.frame.topology == "transfer" and not ({"source", "destination"} & set(core.roles)):
            raise ValueError(f"event {event.id} fallback transfer requires source or destination")
        spec = None
    else:
        spec = registry.get(predicate_id)
        if spec is None:
            raise ValueError(f"event {event.id} uses unregistered predicate: {predicate_id}")
        actual = (core.frame.dynamics, core.frame.topology, core.frame.agency)
        allowed_frames = spec.allowed_frames or ((spec.frame,) if spec.frame is not None else ())
        if allowed_frames and not any(
            actual[:2] == _frame_tuple(expected)[:2]
            and (actual[2] == _frame_tuple(expected)[2]
                 or "unknown" in (actual[2], _frame_tuple(expected)[2]))
            for expected in allowed_frames
        ):
            raise ValueError(f"event {event.id} frame does not match predicate {predicate_id}")
        missing = sorted(set(spec.required_roles) - set(core.roles))
        if missing:
            raise ValueError(f"event {event.id} is missing required roles: {missing}")

    for qualifier in event.qualifiers:
        if qualifier.type in NON_LIFECYCLE_QUALIFIER_VALUES:
            allowed = NON_LIFECYCLE_QUALIFIER_VALUES[qualifier.type]
        else:
            transitions = spec.lifecycle.transitions.get(qualifier.type, ()) if spec and spec.lifecycle else ()
            allowed = frozenset(value for edge in transitions for value in edge)
        if not allowed or qualifier.value not in allowed:
            raise ValueError(
                f"event {event.id} has unsupported {qualifier.type} qualifier: {qualifier.value}"
            )


def validate_event_extraction(data: Any, registry) -> EventExtractionResult:
    """按结构模型和显式 Registry 校验抽取结果。"""
    result = data if isinstance(data, EventExtractionResult) else EventExtractionResult.model_validate(data)
    for event in result.events:
        _validate_event_semantics(event, registry)
    return result


def event_extraction_json_schema(registry) -> dict[str, Any]:
    """生成结构 Schema，并把当前 Registry 的谓词 ID 固化为封闭枚举。"""
    schema = EventExtractionResult.model_json_schema()
    predicate = schema.get("$defs", {}).get("ExtractedPredicate")
    if predicate:
        predicate["properties"]["id"] = {
            "anyOf": [
                {"type": "string", "enum": list(registry)},
                {"type": "null"},
            ]
        }
    return schema
