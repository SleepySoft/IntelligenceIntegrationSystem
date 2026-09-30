from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from itertools import groupby
from typing import Iterable

from .models import EventRecord, QualifierObservation, StateProjection
from .specs import LifecycleSpec
from .temporal import effective_time


STATE_TYPES = frozenset({"phase", "intention", "authorization", "directive"})


def assertion_limited(event: EventRecord) -> bool:
    """这些限定描述主张，不能把同时出现的 phase 当成肯定发生。"""
    for q in event.qualifiers:
        if q.scope != "event":
            continue
        if q.type == "polarity" and q.value not in {"positive", "affirmative", "true"}:
            return True
        if q.type == "epistemic" and q.value not in {"asserted", "certain", "confirmed", "verified", "factual", "known"}:
            return True
        if q.type == "modality" and q.value not in {"actual", "factual", "asserted"}:
            return True
    return False


def project_state(events: Iterable[EventRecord], lifecycle: LifecycleSpec | None = None) -> StateProjection:
    events = tuple(events)
    observations = tuple(
        QualifierObservation(event.uuid, event.intelligence_uuid, q, effective_time(event, q), event.observed_at)
        for event in sorted(events, key=lambda e: str(e.uuid)) for q in event.qualifiers
    )
    groups = defaultdict(list)
    conflicts, unknown = [], []
    denials = []
    for event in events:
        limited = assertion_limited(event)
        for q in event.qualifiers:
            if q.scope != "event":
                continue
            if (q.type == "epistemic" and q.value == "denied") or (
                q.type == "polarity" and q.value in {"negative", "negated", "false"}
            ):
                denials.append(event.uuid)
            if q.type not in STATE_TYPES or limited:
                continue
            edges = lifecycle.transitions.get(q.type) if lifecycle is not None else None
            if edges is not None and q.value not in {value for pair in edges for value in pair}:
                unknown.append(f"{q.type} 状态未定义: {q.value}; observation={event.uuid}")
                continue
            # 意图、授权、指令可能属于不同主体，不能跨主体覆盖。
            owners = tuple(sorted(q.by, key=str)) if q.type != "phase" else ()
            groups[(q.type, owners)].append(
                QualifierObservation(event.uuid, event.intelligence_uuid, q, effective_time(event, q), event.observed_at)
            )

    by_type = defaultdict(list)
    floor = datetime.min.replace(tzinfo=timezone.utc)
    for (kind, owners), entries in sorted(groups.items(), key=lambda x: str(x[0])):
        entries.sort(key=lambda x: (x.effective_at is None, x.effective_at or floor, str(x.event_uuid), x.qualifier.id))
        current = None
        supports = set()
        current_time = None
        for when, batch_iter in groupby(entries, key=lambda x: x.effective_at):
            batch = tuple(batch_iter)
            values = {x.qualifier.value for x in batch}
            refs = ",".join(str(x.event_uuid) for x in batch)
            if len(values) != 1:
                conflicts.append(f"{kind} 同一有效时间存在不同主张 {sorted(values)}; observations={refs}")
                current = None
                supports.clear()
                continue
            value = next(iter(values))
            if current is None:
                current, current_time = value, when
                supports = {x.event_uuid for x in batch}
            elif value == current:
                supports.update(x.event_uuid for x in batch)
                current_time = when or current_time
            elif when is None or current_time is None:
                unknown.append(f"{kind} 有效时间缺失，不能判定 {current}->{value}; observations={refs}")
            elif lifecycle is None or kind not in lifecycle.transitions:
                unknown.append(f"{kind} 未配置状态迁移，不能判定 {current}->{value}; observations={refs}")
            elif lifecycle.allows(kind, current, value):
                current, current_time = value, when
                supports = {x.event_uuid for x in batch}
            else:
                conflicts.append(f"{kind} 非法状态迁移 {current}->{value}; observations={refs}")
        if current is not None:
            by_type[kind].append((current, tuple(sorted(supports, key=str)), owners))

    values, supporting = {}, {}
    for kind, states in by_type.items():
        choices = {value for value, _, _ in states}
        if len(choices) > 1:
            conflicts.append(f"{kind} 不同主体的状态不一致: {sorted(choices)}")
            continue
        values[kind] = next(iter(choices))
        supporting[kind] = tuple(sorted({ref for _, refs, _ in states for ref in refs}, key=str))
    if denials and values:
        conflicts.append("事件状态主张与否认/否定并存; observations=" + ",".join(str(x) for x in sorted(set(denials), key=str)))
    return StateProjection(
        values, supporting, observations, tuple(conflicts), tuple(unknown),
        "disputed" if conflicts else "unverified",
    )
