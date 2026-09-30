from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from typing import Mapping
from .models import CanonicalEvent, EventRecord, MatchDecision, MatchResult
from .specs import IdentitySpec, PredicateSpec, generic_spec
from .registry import as_registry
from .analyzer import EventAnalyzer

class CanonicalEventMatcher:
    def __init__(self, specs: Mapping[str, PredicateSpec] | None = None):
        self.specs = as_registry(specs)

    def match(self, observation: EventRecord, candidate: CanonicalEvent) -> MatchResult:
        matched, unknown, conflicts = [], [], []
        if observation.predicate.id != candidate.predicate_id:
            return MatchResult(MatchDecision.DIFFERENT, 0, candidate.uuid, conflicts=("predicate冲突",))
        spec = self.specs.get(observation.predicate.id or "")
        identity = spec.identity if spec else generic_spec(observation.predicate.id)

        role_score, hard_role_conflict = self._roles(observation, candidate, identity, matched, unknown, conflicts)
        if hard_role_conflict:
            return MatchResult(MatchDecision.DIFFERENT, 0, candidate.uuid, tuple(matched), tuple(unknown), tuple(conflicts))

        time_score, time_conflict = self._time(observation, candidate, identity, matched, unknown, conflicts)
        if time_conflict:
            return MatchResult(MatchDecision.DIFFERENT, 0, candidate.uuid, tuple(matched), tuple(unknown), tuple(conflicts))

        location_score, location_conflict = self._location(observation, candidate, identity, matched, unknown, conflicts)
        if location_conflict:
            return MatchResult(MatchDecision.DIFFERENT, 0, candidate.uuid, tuple(matched), tuple(unknown), tuple(conflicts))

        attribute_score = self._attributes(observation, matched, unknown)
        lifecycle_score, qualifier_changed = self._lifecycle(observation, candidate, matched, unknown)
        total = identity.role_weight + identity.time_weight + identity.location_weight + identity.attribute_weight + identity.lifecycle_weight
        raw = (identity.role_weight*role_score + identity.time_weight*time_score +
               identity.location_weight*location_score + identity.attribute_weight*attribute_score +
               identity.lifecycle_weight*lifecycle_score)
        score = round(raw / total, 4) if total else 0
        if score >= identity.auto_merge_threshold:
            decision = MatchDecision.STATE_UPDATE if qualifier_changed else MatchDecision.SAME_EVENT
        elif score >= identity.review_threshold:
            decision = MatchDecision.AMBIGUOUS
        else:
            decision = MatchDecision.DIFFERENT
        return MatchResult(decision, score, candidate.uuid, tuple(matched), tuple(unknown), tuple(conflicts))

    def resolve(self, observation: EventRecord, candidates: tuple[CanonicalEvent, ...]) -> MatchResult:
        if not candidates:
            return MatchResult(MatchDecision.NEW_EVENT, 1.0)
        results = sorted((self.match(observation, c) for c in candidates), key=lambda x: x.score, reverse=True)
        best = results[0]
        if best.decision in (MatchDecision.SAME_EVENT, MatchDecision.STATE_UPDATE):
            spec = self.specs.get(observation.predicate.id or "")
            margin = spec.identity.auto_merge_margin if spec else .15
            second = results[1].score if len(results) > 1 else 0
            if best.score - second < margin:
                return MatchResult(MatchDecision.AMBIGUOUS, best.score, best.candidate_uuid,
                                   best.matched, best.unknown + ("候选领先差值不足",), best.conflicts)
        return best

    def create_canonical(self, event: EventRecord) -> CanonicalEvent:
        identity = self._identity_spec(event)
        identity_roles = {r: tuple(sorted(event.entities_for_role(r), key=str)) for r in identity.identity_roles if event.entities_for_role(r)}
        state = EventAnalyzer().current_state((event,))
        start, end = self._event_bounds(event)
        return CanonicalEvent(uuid=uuid4(), predicate_id=event.predicate.id, frame=event.frame,
            identity_roles=identity_roles, observation_event_uuids=(event.uuid,), current_qualifiers=state,
            location_entity_uuids=event.location_entity_uuids, first_observed_at=event.observed_at,
            last_observed_at=event.observed_at, event_time_start=start, event_time_end=end)

    def update_canonical(self, candidate: CanonicalEvent, events: tuple[EventRecord, ...]) -> CanonicalEvent:
        ordered = sorted(events, key=lambda e: e.observed_at or datetime.min.replace(tzinfo=timezone.utc))
        identity = self._identity_spec(ordered[0])
        roles = {}
        for role in identity.identity_roles:
            values = {x for e in ordered for x in e.entities_for_role(role)}
            if values: roles[role] = tuple(sorted(values, key=str))
        state = EventAnalyzer().current_state(ordered)
        observed = [e.observed_at for e in ordered if e.observed_at]
        starts, ends = zip(*(self._event_bounds(e) for e in ordered))
        return CanonicalEvent(uuid=candidate.uuid, predicate_id=candidate.predicate_id, frame=candidate.frame,
            identity_roles=roles, observation_event_uuids=tuple(e.uuid for e in ordered), current_qualifiers=state,
            location_entity_uuids=tuple(sorted({x for e in ordered for x in e.location_entity_uuids}, key=str)),
            first_observed_at=min(observed) if observed else None, last_observed_at=max(observed) if observed else None,
            event_time_start=min((x for x in starts if x), default=None), event_time_end=max((x for x in ends if x), default=None),
            unresolved_conflicts=candidate.unresolved_conflicts, version=candidate.version+1)

    def _identity_spec(self, event: EventRecord) -> IdentitySpec:
        spec = self.specs.get(event.predicate.id or "")
        return spec.identity if spec else generic_spec(event.predicate.id)

    @staticmethod
    def _roles(event, candidate, spec, matched, unknown, conflicts):
        scores=[]; hard=False
        for role in spec.identity_roles:
            left=set(event.entities_for_role(role)); right=set(candidate.identity_roles.get(role, ()))
            if not left or not right:
                unknown.append(f"身份角色{role}缺失"); continue
            if left == right:
                scores.append(1); matched.append(f"身份角色{role}一致")
            elif left & right:
                scores.append(len(left&right)/len(left|right)); matched.append(f"身份角色{role}部分重叠")
            else:
                conflicts.append(f"身份角色{role}冲突"); hard=True
        return (sum(scores)/len(scores) if scores else .5), hard

    @staticmethod
    def _time(event, candidate, spec, matched, unknown, conflicts):
        if spec.time_mode == "ignore": return 1, False
        e_start, e_end = CanonicalEventMatcher._event_bounds(event)
        c_start, c_end = candidate.event_time_start, candidate.event_time_end
        if not e_start or not c_start:
            unknown.append("事件时间缺失"); return .5, False
        try:
            a=datetime.fromisoformat(e_start); b=datetime.fromisoformat(c_start)
            delta=abs(a-b)
            if spec.time_tolerance and delta > spec.time_tolerance and spec.time_mode == "occurrence":
                conflicts.append("点事件时间超出容忍窗口"); return 0, True
            if spec.time_tolerance:
                score=max(0, 1-delta.total_seconds()/spec.time_tolerance.total_seconds())
            else: score=1
            matched.append("事件时间兼容"); return score, False
        except ValueError:
            if e_start == c_start:
                matched.append("事件时间字符串一致"); return 1, False
            unknown.append("事件时间精度不足"); return .5, False

    @staticmethod
    def _location(event, candidate, spec, matched, unknown, conflicts):
        if spec.location_mode == "ignore": return 1, False
        left=set(event.location_entity_uuids); right=set(candidate.location_entity_uuids)
        if not left or not right:
            unknown.append("地点缺失"); return .5, False
        if left & right:
            matched.append("地点一致或重叠"); return len(left&right)/len(left|right), False
        if spec.location_mode == "strict":
            conflicts.append("关键地点冲突"); return 0, True
        return 0, False

    @staticmethod
    def _attributes(event, matched, unknown):
        if not event.attributes:
            unknown.append("身份属性缺失"); return .5
        matched.append("存在可比较属性"); return .7

    @staticmethod
    def _lifecycle(event, candidate, matched, unknown):
        incoming={q.type:q.value for q in event.qualifiers if q.scope=="event"}
        if not incoming:
            unknown.append("生命周期限定缺失"); return .5, False
        changed=any(candidate.current_qualifiers.get(k)!=v for k,v in incoming.items())
        matched.append("生命周期状态可用于更新" if changed else "生命周期状态一致")
        return 1, changed

    @staticmethod
    def _event_bounds(event):
        def val(*keys):
            for k in keys:
                x=event.time.get(k)
                if x and x.normalized: return x.normalized
            return None
        start=val("start_time","event_time","effective_time","expected_start_time")
        end=val("end_time","event_time","expected_end_time") or start
        return start,end
