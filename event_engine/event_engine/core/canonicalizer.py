from __future__ import annotations

from dataclasses import replace
from typing import Mapping
from uuid import uuid4

from .analyzer import EventAnalyzer
from ..schema.models import CanonicalEvent, EventRecord, MatchDecision, MatchResult
from .registry import as_registry
from ..schema.specs import IdentitySpec, PredicateSpec
from .temporal import event_bounds, expression_position, position


class CanonicalEventMatcher:
    def __init__(self, specs: Mapping[str, PredicateSpec] | None = None):
        self.specs = as_registry(specs)

    def match(self, observation: EventRecord, candidate: CanonicalEvent) -> MatchResult:
        matched, unknown, conflicts = [], [], []
        def result(decision, score=0.0, coverage=0.0):
            return MatchResult(decision, round(score, 4), candidate.uuid,
                               tuple(matched), tuple(unknown), tuple(conflicts), round(coverage, 4))

        if observation.predicate.id != candidate.predicate_id:
            conflicts.append("predicate冲突")
            return result(MatchDecision.DIFFERENT)
        spec = self.specs.get(observation.predicate.id or "")
        identity = spec.identity if spec else _generic_identity()
        for ir in (observation.ir, replace(observation.ir, frame=candidate.frame)):
            errors = self.specs.validate(ir)
            if errors:
                conflicts.extend(errors)
                return result(MatchDecision.DIFFERENT)
        frames = spec.allowed_frames if spec else ()
        explicit_variants = bool(frames)  # 两侧已通过配置约束（含 unknown agency 的兼容检查）。
        if not explicit_variants and (
            observation.frame.dynamics != candidate.frame.dynamics
            or observation.frame.topology != candidate.frame.topology
            or (observation.frame.agency.value != "unknown" and candidate.frame.agency.value != "unknown"
                and observation.frame.agency != candidate.frame.agency)
        ):
            conflicts.append("Frame冲突")
            return result(MatchDecision.DIFFERENT)

        role_score, role_coverage, ready = self._roles(observation, candidate, identity, matched, unknown, conflicts)
        attribute_score, attribute_coverage, attributes_ready = self._attributes(
            observation, candidate, identity, matched, unknown, conflicts)
        dimensions = [(identity.role_weight, role_score, role_coverage)]
        time_score = None
        if identity.time_mode != "ignore" and (identity.time_weight > 0 or identity.auto_merge_require_time):
            time_score = self._time(observation, candidate, identity, matched, unknown, conflicts)
            dimensions.append((identity.time_weight, time_score or 0, float(time_score is not None)))
        location_score = None
        if identity.location_mode != "ignore" and (identity.location_weight > 0 or identity.location_mode == "strict"):
            location_score = self._location(observation, candidate, identity, matched, unknown, conflicts)
            dimensions.append((identity.location_weight, location_score or 0, float(location_score is not None)))
        if identity.identity_attributes or identity.auto_merge_required_attributes:
            dimensions.append((identity.attribute_weight, attribute_score, attribute_coverage))

        # 生命周期进展不能给身份证据加分；没有比较的属性也不能产生正分。
        total = sum(weight for weight, _, _ in dimensions)
        score = sum(weight * value for weight, value, _ in dimensions) / total if total else 0
        coverage = sum(weight * known for weight, _, known in dimensions) / total if total else 0
        if conflicts:
            return result(MatchDecision.DIFFERENT, score, coverage)
        if spec is None:
            unknown.append("谓词无身份规则，不能自动合并")
            ready = False
        if (identity.auto_merge_require_time or (
            identity.time_mode in {"occurrence", "interval"} and identity.time_weight > 0
        )) and time_score is None:
            unknown.append("自动合并所需事件时间不足")
            ready = False
        if identity.location_mode == "strict" and location_score is None:
            unknown.append("自动合并所需关键地点不足")
            ready = False
        if not ready or not attributes_ready or coverage < identity.min_evidence_coverage:
            return result(MatchDecision.AMBIGUOUS, score, coverage)
        if score >= identity.auto_merge_threshold:
            incoming = EventAnalyzer(self.specs).current_state((observation,))
            changed = any(candidate.current_qualifiers.get(k) != v for k, v in incoming.items())
            return result(MatchDecision.STATE_UPDATE if changed else MatchDecision.SAME_EVENT, score, coverage)
        return result(MatchDecision.AMBIGUOUS if score >= identity.review_threshold else MatchDecision.DIFFERENT,
                      score, coverage)

    def resolve(self, observation: EventRecord, candidates: tuple[CanonicalEvent, ...]) -> MatchResult:
        for candidate in candidates:
            if observation.uuid in candidate.observation_event_uuids:
                return MatchResult(MatchDecision.DUPLICATE, 1, candidate.uuid, matched=("同一观察已绑定",),
                                   evidence_coverage=1)
        if not candidates:
            return MatchResult(MatchDecision.NEW_EVENT, 0)
        results = sorted((self.match(observation, c) for c in candidates), key=lambda r: r.score, reverse=True)
        viable = [r for r in results if r.decision != MatchDecision.DIFFERENT]
        if not viable:
            best = results[0]
            return replace(best, decision=MatchDecision.NEW_EVENT, score=0, candidate_uuid=None)
        best = viable[0]
        if best.decision in (MatchDecision.SAME_EVENT, MatchDecision.STATE_UPDATE):
            identity = self._identity_spec(observation)
            if len(viable) > 1 and best.score - viable[1].score < identity.auto_merge_margin:
                return replace(best, decision=MatchDecision.AMBIGUOUS,
                               unknown=best.unknown + ("候选领先差值不足",))
        return best

    def create_canonical(self, event: EventRecord) -> CanonicalEvent:
        identity = self._identity_spec(event)
        projection = EventAnalyzer(self.specs).project_state((event,))
        start, end = event_bounds(event)
        return CanonicalEvent(
            uuid=uuid4(), predicate_id=event.predicate.id, frame=event.frame,
            identity_roles=self._collect_roles((event,), identity.identity_roles),
            discriminator_roles=self._collect_roles((event,), identity.discriminator_roles),
            identity_attributes=self._collect_attributes((event,), identity),
            observation_event_uuids=(event.uuid,), current_qualifiers=projection.values,
            state_projection=projection, location_entity_uuids=event.location_entity_uuids,
            first_observed_at=event.observed_at, last_observed_at=event.observed_at,
            event_time_start=start, event_time_end=end, unresolved_conflicts=projection.conflicts,
        )

    def update_canonical(self, candidate: CanonicalEvent, events: tuple[EventRecord, ...]) -> CanonicalEvent:
        if not events:
            raise ValueError("CanonicalEvent 成员不能为空")
        if any(e.predicate.id != candidate.predicate_id for e in events):
            raise ValueError("CanonicalEvent 成员谓词不一致")
        known_ids = set(candidate.observation_event_uuids)
        if not known_ids <= {e.uuid for e in events}:
            raise ValueError("重算缺少已有观察")
        # 检查新成员与每个旧成员，避免聚合后的时间/地点并集形成桥接误合并。
        old_members = [e for e in events if e.uuid in known_ids]
        for incoming in (e for e in events if e.uuid not in known_ids):
            for member in old_members:
                compared = self.match(incoming, self.create_canonical(member))
                if compared.conflicts:
                    raise ValueError("成员身份冲突: " + "; ".join(compared.conflicts))
        identity = self._identity_spec(events[0])
        projection = EventAnalyzer(self.specs).project_state(events)
        observed = [e.observed_at for e in events if e.observed_at]
        bounds = [event_bounds(e) for e in events]
        starts = [start for start, _ in bounds if start]
        ends = [end for _, end in bounds if end]
        return replace(
            candidate, identity_roles=self._collect_roles(events, identity.identity_roles),
            discriminator_roles=self._collect_roles(events, identity.discriminator_roles),
            identity_attributes=self._collect_attributes(events, identity),
            observation_event_uuids=tuple(sorted({e.uuid for e in events}, key=str)),
            current_qualifiers=projection.values, state_projection=projection,
            location_entity_uuids=tuple(sorted({x for e in events for x in e.location_entity_uuids}, key=str)),
            first_observed_at=min(observed) if observed else None,
            last_observed_at=max(observed) if observed else None,
            event_time_start=min(starts, key=lambda x: position(x)[0]) if starts else None,
            event_time_end=max(ends, key=lambda x: position(x)[1]) if ends else None,
            unresolved_conflicts=projection.conflicts, version=candidate.version + 1,
        )

    def _identity_spec(self, event):
        spec = self.specs.get(event.predicate.id or "")
        return spec.identity if spec else _generic_identity()

    @staticmethod
    def _collect_roles(events, roles):
        result = {}
        for role in roles:
            sets = {frozenset(e.entities_for_role(role)) for e in events if e.entities_for_role(role)}
            if len(sets) > 1:
                raise ValueError(f"身份角色 {role} 存在多个不同取值，不能取并集")
            if sets:
                result[role] = tuple(sorted(next(iter(sets)), key=str))
        return result

    @staticmethod
    def _roles(event, candidate, spec, matched, unknown, conflicts):
        scores, known = [], 0
        required = spec.identity_roles if spec.auto_merge_required_roles is None else spec.auto_merge_required_roles
        ready = bool(spec.identity_roles)
        for role in spec.identity_roles:
            left, right = set(event.entities_for_role(role)), set(candidate.identity_roles.get(role, ()))
            if not left or not right:
                unknown.append(f"身份角色{role}缺失")
                scores.append(0)
                if role in required:
                    ready = False
            else:
                known += 1
                scores.append(len(left & right) / len(left | right))
                if not left & right:
                    conflicts.append(f"身份角色{role}冲突")
                elif left == right:
                    matched.append(f"身份角色{role}一致")
                else:
                    unknown.append(f"身份角色{role}仅部分重叠")
                    if role in required:
                        ready = False
        for role in spec.discriminator_roles:
            left, right = set(event.entities_for_role(role)), set(candidate.discriminator_roles.get(role, ()))
            if left and right:
                if left != right:
                    conflicts.append(f"区分角色{role}冲突")
                else:
                    matched.append(f"区分角色{role}一致")
            else:
                unknown.append(f"区分角色{role}缺失")
        return (sum(scores) / len(scores) if scores else 0,
                known / len(scores) if scores else 0, ready)

    @staticmethod
    def _attribute_equal(left, right):
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            return None
        # 原文、展示文本不参与身份；单位不明时拒绝把数值直接比较。
        for field in ("currency", "type"):
            if left.get(field) is not None and right.get(field) is not None and left[field] != right[field]:
                return False
        if left.get("unit") != right.get("unit"):
            return None
        if left.get("currency") != right.get("currency") or left.get("type") != right.get("type"):
            return None
        if "value" not in left or "value" not in right:
            return None
        return left["value"] == right["value"]

    @staticmethod
    def _attributes(event, candidate, spec, matched, unknown, conflicts):
        keys = tuple(dict.fromkeys(spec.identity_attributes + spec.auto_merge_required_attributes))
        scores, known, ready = [], 0, True
        for key in keys:
            left, right = event.attributes.get(key), candidate.identity_attributes.get(key)
            equal = CanonicalEventMatcher._attribute_equal(left, right) if left is not None and right is not None else None
            if equal is None:
                unknown.append(f"身份属性{key}缺失或单位不可比较")
                scores.append(0)
                if key in spec.auto_merge_required_attributes:
                    ready = False
            else:
                known += 1
                scores.append(float(equal))
                if equal:
                    matched.append(f"身份属性{key}一致")
                else:
                    conflicts.append(f"身份属性{key}冲突")
        return sum(scores) / len(scores) if scores else 0, known / len(scores) if scores else 0, ready

    @staticmethod
    def _collect_attributes(events, spec):
        result = {}
        for key in dict.fromkeys(spec.identity_attributes + spec.auto_merge_required_attributes):
            for event in sorted(events, key=lambda e: str(e.uuid)):
                value = event.attributes.get(key)
                if value is None:
                    continue
                if key in result and CanonicalEventMatcher._attribute_equal(result[key], value) is False:
                    raise ValueError(f"身份属性{key}冲突")
                result.setdefault(key, value)
        return result

    @staticmethod
    def _time(event, candidate, spec, matched, unknown, conflicts):
        e_start, e_end = event_bounds(event)
        a, b = position(e_start), position(candidate.event_time_start)
        if a is None or b is None:
            unknown.append("事件时间缺失或无法解析")
            return None
        limit = spec.max_time_uncertainty
        if limit is not None and (a[1] - a[0] > limit or b[1] - b[0] > limit):
            unknown.append("事件时间精度不足")
            return None
        if spec.time_mode == "interval":
            ae, be = position(e_end), position(candidate.event_time_end)
            if ae is None or be is None:
                unknown.append("事件区间端点缺失")
                return None
            if ae[1] <= a[0] or be[1] <= b[0]:
                conflicts.append("事件时间区间非法")
                return 0
            if min(ae[1], be[1]) > max(a[0], b[0]):
                matched.append("事件时间区间重叠")
                return 1
            delta = max(a[0] - be[1], b[0] - ae[1])
        else:
            # 同一天的报道具有重叠的精度范围，不将日期当作一个精确午夜。
            if min(a[1], b[1]) > max(a[0], b[0]):
                matched.append("事件时间范围重叠")
                return 1
            delta = abs(a[0] - b[0])
        if spec.time_tolerance is not None and delta > spec.time_tolerance:
            conflicts.append("事件时间超出容忍窗口")
            return 0
        if spec.time_tolerance is None:
            unknown.append("事件时间不重叠且未配置容忍窗口")
            return None
        matched.append("事件时间在容忍窗口内")
        return max(0, 1 - delta.total_seconds() / spec.time_tolerance.total_seconds())

    @staticmethod
    def _location(event, candidate, spec, matched, unknown, conflicts):
        left, right = set(event.location_entity_uuids), set(candidate.location_entity_uuids)
        if not left or not right:
            unknown.append("地点缺失")
            return None
        if left & right:
            matched.append("地点一致或重叠")
            return len(left & right) / len(left | right)
        if spec.location_mode == "strict":
            conflicts.append("关键地点冲突")
        return 0

    _event_bounds = staticmethod(event_bounds)


def _generic_identity() -> IdentitySpec:
    """未注册谓词的保守兜底；匹配器仍禁止据此自动合并。"""
    return IdentitySpec(identity_roles=("subject",), auto_merge_threshold=.92, review_threshold=.75)
