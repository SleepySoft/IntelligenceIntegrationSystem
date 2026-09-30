import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from event_engine.configs import default_registry
from event_engine.core import (
    CanonicalEventMatcher, EventAnalyzer, EventEngine, EventRecord,
    LifecycleSpec, MatchDecision, PredicateRegistry,
)
from event_engine.ir import (
    Agency, Dynamics, Frame, Predicate, Qualifier, RoleBinding, SemanticRoleGroup,
    TimeExpression, Topology,
)
from event_engine.query.memory import InMemoryCanonicalEventRepository, InMemoryEventRepository


def time(day, precision="day", approximate=False):
    return TimeExpression(day, precision, approximate, day)


class SemanticFixTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b, self.c, self.location = uuid4(), uuid4(), uuid4(), uuid4()
        self.registry = default_registry()
        self.matcher = CanonicalEventMatcher(self.registry)
        self.analyzer = EventAnalyzer(self.registry)
        self.engine = EventEngine(InMemoryEventRepository(), InMemoryCanonicalEventRepository(), registry=self.registry)

    def acquire(self, day="2026-09-01", phase=None, qualifiers=(), observed="2026-09-30"):
        if phase is not None:
            qualifiers = (Qualifier("Q1", "phase", phase),) + qualifiers
        return EventRecord(
            uuid4(), uuid4(), "E1", Frame(Dynamics.CHANGE, Topology.TRANSFER, Agency.AGENTIVE),
            Predicate("acquire", "收购"),
            (RoleBinding("acquirer", self.a, SemanticRoleGroup.AGENT), RoleBinding("asset", self.b, SemanticRoleGroup.THEME)),
            time={"event_time": time(day)} if day else {}, qualifiers=qualifiers,
            observed_at=datetime.fromisoformat(observed).replace(tzinfo=timezone.utc),
        )

    def bind(self, event):
        self.engine.register_event(event)
        return self.engine.resolve_canonical(event.uuid)

    def attack(self, day="2026-09-01"):
        return replace(
            self.acquire(day), frame=self.registry["attack"].frame, predicate=Predicate("attack", "袭击"),
            role_bindings=(RoleBinding("actor", self.a), RoleBinding("target", self.b)),
            location_entity_uuids=(self.location,),
        )

    def test_missing_acquisition_asset_is_unknown_and_never_auto_merged(self):
        canonical, _ = self.bind(self.acquire())
        incomplete = replace(self.acquire(phase="completed"), role_bindings=(RoleBinding("acquirer", self.a),))
        resolved, result = self.bind(incomplete)
        self.assertIsNone(resolved)
        self.assertEqual(MatchDecision.AMBIGUOUS, result.decision)
        self.assertTrue(any("asset" in reason for reason in result.unknown))
        self.assertFalse(result.conflicts)
        self.assertLess(result.evidence_coverage, 1)
        self.assertEqual(canonical, self.engine.canonicals.get(canonical.uuid))
        self.assertIsNotNone(self.engine.events.get(incomplete.uuid))

    def test_missing_role_cannot_be_compensated_by_other_weights(self):
        spec = self.registry["acquire"]
        identity = replace(spec.identity, role_weight=.01, time_weight=.99)
        matcher = CanonicalEventMatcher(PredicateRegistry({"acquire": replace(spec, identity=identity)}))
        candidate = matcher.create_canonical(self.acquire())
        event = replace(self.acquire(), role_bindings=(RoleBinding("acquirer", self.a),))
        result = matcher.match(event, candidate)
        self.assertGreater(result.score, .9)
        self.assertEqual(MatchDecision.AMBIGUOUS, result.decision)

    def test_partial_identity_sets_are_not_treated_as_complete_evidence(self):
        candidate = self.matcher.create_canonical(self.acquire())
        event = replace(self.acquire(), role_bindings=self.acquire().role_bindings + (RoleBinding("asset", self.c),))
        self.assertEqual(MatchDecision.AMBIGUOUS, self.matcher.match(event, candidate).decision)

    def test_different_seller_prevents_merge(self):
        first = replace(self.acquire(), role_bindings=self.acquire().role_bindings + (RoleBinding("seller", self.c),))
        second = replace(self.acquire(), role_bindings=self.acquire().role_bindings + (RoleBinding("seller", uuid4()),))
        result = self.matcher.match(second, self.matcher.create_canonical(first))
        self.assertEqual(MatchDecision.DIFFERENT, result.decision)
        self.assertTrue(any("seller" in reason for reason in result.conflicts))

    def attribute_matcher(self):
        spec = self.registry["acquire"]
        identity = replace(spec.identity, identity_attributes=("currency",), auto_merge_required_attributes=("currency",))
        return CanonicalEventMatcher(PredicateRegistry({"acquire": replace(spec, identity=identity)}))

    def test_identity_attributes_are_compared_against_candidate(self):
        matcher = self.attribute_matcher()
        first = replace(self.acquire(), attributes={"currency": {"value": "USD"}})
        second = replace(self.acquire(), attributes={"currency": {"value": "EUR"}})
        result = matcher.match(second, matcher.create_canonical(first))
        self.assertEqual(MatchDecision.DIFFERENT, result.decision)
        self.assertTrue(any("currency" in reason for reason in result.conflicts))

    def test_required_identity_attribute_missing_is_ambiguous(self):
        matcher = self.attribute_matcher()
        first = replace(self.acquire(), attributes={"currency": {"value": "USD"}})
        result = matcher.match(self.acquire(), matcher.create_canonical(first))
        self.assertEqual(MatchDecision.AMBIGUOUS, result.decision)
        self.assertFalse(result.conflicts)

    def test_non_identity_amount_change_does_not_create_new_event(self):
        first = replace(self.acquire(), attributes={"amount": {"value": 4.2, "currency": "USD"}})
        second = replace(self.acquire(), attributes={"amount": {"value": 4.5, "currency": "USD"}})
        self.assertEqual(MatchDecision.SAME_EVENT, self.matcher.match(second, self.matcher.create_canonical(first)).decision)

    def test_unknown_units_do_not_create_false_attribute_conflict(self):
        spec = self.registry["acquire"]
        identity = replace(spec.identity, identity_attributes=("amount",), auto_merge_required_attributes=("amount",))
        matcher = CanonicalEventMatcher(PredicateRegistry({"acquire": replace(spec, identity=identity)}))
        first = replace(self.acquire(), attributes={"amount": {"value": 1, "unit": "billion"}})
        second = replace(self.acquire(), attributes={"amount": {"value": 1000, "unit": "million"}})
        result = matcher.match(second, matcher.create_canonical(first))
        self.assertEqual(MatchDecision.AMBIGUOUS, result.decision)
        self.assertFalse(result.conflicts)

    def test_attack_without_time_or_location_needs_review(self):
        candidate = self.matcher.create_canonical(self.attack())
        for event in (replace(self.attack(), time={}), replace(self.attack(), location_entity_uuids=())):
            with self.subTest(event=event):
                self.assertEqual(MatchDecision.AMBIGUOUS, self.matcher.match(event, candidate).decision)

    def test_acquisition_also_requires_time_even_when_role_weight_is_high(self):
        candidate = self.matcher.create_canonical(self.acquire())
        result = self.matcher.match(self.acquire(day=None), candidate)
        self.assertEqual(MatchDecision.AMBIGUOUS, result.decision)
        self.assertTrue(any("时间" in reason for reason in result.unknown))

    def test_month_precision_and_approximate_attack_time_cannot_auto_merge(self):
        candidate = self.matcher.create_canonical(self.attack())
        for expression in (time("2026-09", "month"), time("2026-09-01", approximate=True)):
            event = replace(self.attack(), time={"event_time": expression})
            self.assertEqual(MatchDecision.AMBIGUOUS, self.matcher.match(event, candidate).decision)

    def test_interval_overlap_uses_endpoints_not_only_start_difference(self):
        spec = self.registry["armed_conflict"]
        identity = replace(spec.identity, time_tolerance=timedelta(days=2))
        matcher = CanonicalEventMatcher(PredicateRegistry({"armed_conflict": replace(spec, identity=identity)}))
        first = replace(self.attack(), predicate=Predicate("armed_conflict", "冲突"), frame=spec.frame,
                        role_bindings=(RoleBinding("belligerent", self.a),),
                        time={"start_time": time("2026-01-01"), "end_time": time("2026-12-31")})
        second = replace(first, uuid=uuid4(), time={"start_time": time("2026-11-01"), "end_time": time("2026-11-30")})
        result = matcher.match(second, matcher.create_canonical(first))
        self.assertEqual(MatchDecision.SAME_EVENT, result.decision)
        self.assertIn("事件时间区间重叠", result.matched)

    def test_known_different_occurrence_creates_new_canonical(self):
        first, _ = self.bind(self.attack("2026-09-01"))
        second, result = self.bind(self.attack("2026-09-08"))
        self.assertEqual(MatchDecision.NEW_EVENT, result.decision)
        self.assertNotEqual(first.uuid, second.uuid)

    def test_unregistered_predicate_cannot_auto_merge_from_generic_fallback(self):
        event = replace(self.acquire(), predicate=Predicate("custom:unknown", "未知"),
                        role_bindings=(RoleBinding("subject", self.a),))
        matcher = CanonicalEventMatcher()
        result = matcher.match(event, matcher.create_canonical(event))
        self.assertEqual(MatchDecision.AMBIGUOUS, result.decision)

    def test_late_old_report_does_not_roll_back_completed_phase(self):
        completed = self.acquire("2026-09-01", "completed", observed="2026-09-02")
        canonical, _ = self.bind(completed)
        old_report = self.acquire("2026-05-01", "ongoing", observed="2026-10-01")
        updated, result = self.bind(old_report)
        self.assertEqual(canonical.uuid, updated.uuid)
        self.assertEqual("completed", updated.current_qualifiers["phase"])
        self.assertEqual(MatchDecision.SAME_EVENT, result.decision)
        self.assertEqual((completed.uuid,), updated.state_projection.supporting_event_uuids["phase"])
        self.assertFalse(updated.unresolved_conflicts)

    def test_qualifier_effective_time_overrides_article_event_time(self):
        completed = self.acquire("2026-09-01", "completed")
        historical = self.acquire("2026-10-01", qualifiers=(Qualifier("Q1", "phase", "ongoing", time=time("2026-05-01")),))
        state = self.analyzer.project_state((completed, historical))
        self.assertEqual("completed", state.values["phase"])
        self.assertFalse(state.conflicts)

    def test_denial_is_preserved_with_claimant_without_overwriting_completion(self):
        completed = self.acquire(phase="completed")
        self.bind(completed)
        denial = self.acquire(qualifiers=(Qualifier("Q1", "epistemic", "denied", by=(self.c,)),
                                         Qualifier("Q2", "phase", "completed")))
        updated, result = self.bind(denial)
        projection = updated.state_projection
        self.assertEqual("completed", projection.values["phase"])
        self.assertNotIn("epistemic", updated.current_qualifiers)
        self.assertEqual("disputed", projection.assertion_status)
        evidence = next(x for x in projection.observations if x.qualifier.type == "epistemic")
        self.assertEqual((self.c,), evidence.qualifier.by)
        self.assertEqual(denial.intelligence_uuid, evidence.intelligence_uuid)
        self.assertEqual(MatchDecision.SAME_EVENT, result.decision)
        self.assertTrue(result.conflicts)

    def test_hypothetical_completion_does_not_become_reported_phase(self):
        for kind, value in (("epistemic", "possible"), ("modality", "predicted"), ("polarity", "negative")):
            with self.subTest(kind=kind):
                event = self.acquire(phase="completed", qualifiers=(Qualifier("Q2", kind, value),))
                projection = self.analyzer.project_state((event,))
                self.assertNotIn("phase", projection.values)
                self.assertEqual(2, len(projection.observations))

    def test_illegal_completed_to_ongoing_is_a_state_conflict_not_new_identity(self):
        canonical, _ = self.bind(self.acquire("2026-09-01", "completed"))
        updated, result = self.bind(self.acquire("2026-09-02", "ongoing"))
        self.assertEqual(canonical.uuid, updated.uuid)
        self.assertEqual("completed", updated.current_qualifiers["phase"])
        self.assertTrue(any("非法状态迁移" in reason for reason in updated.unresolved_conflicts))
        self.assertEqual("disputed", updated.state_projection.assertion_status)
        self.assertEqual(MatchDecision.SAME_EVENT, result.decision)

    def test_equal_effective_time_conflicting_reports_are_not_arbitrarily_overwritten(self):
        first = self.acquire(phase="completed", observed="2026-09-02")
        second = self.acquire(phase="ongoing", observed="2026-10-01")
        state = self.analyzer.project_state((first, second))
        reversed_state = self.analyzer.project_state((second, first))
        self.assertEqual(state, reversed_state)
        self.assertNotIn("phase", state.values)
        self.assertTrue(state.conflicts)
        self.assertEqual(2, len(state.observations))

    def test_authorizations_from_two_authorities_are_not_collapsed(self):
        first = self.acquire(qualifiers=(Qualifier("Q1", "authorization", "approved", by=(self.a,)),))
        second = self.acquire(qualifiers=(Qualifier("Q1", "authorization", "denied", by=(self.c,)),))
        state = self.analyzer.project_state((first, second))
        self.assertNotIn("authorization", state.values)
        self.assertTrue(any("不同主体" in reason for reason in state.conflicts))

    def test_unparseable_qualifier_time_does_not_use_article_arrival_as_effective_time(self):
        first = self.acquire(phase="completed")
        other = self.acquire(qualifiers=(Qualifier("Q1", "phase", "ongoing", time=time("last week")),))
        state = self.analyzer.project_state((first, other))
        self.assertEqual("completed", state.values["phase"])
        self.assertTrue(state.unknown)

    def test_lifecycle_does_not_invent_unobserved_intermediate_phases(self):
        state = self.analyzer.project_state((self.acquire(phase="completed"),))
        self.assertEqual({"phase": "completed"}, state.values)
        self.assertEqual(1, len(state.observations))
        self.assertEqual("unverified", state.assertion_status)

    def test_lifecycle_can_skip_unreported_intermediate_steps(self):
        state = self.analyzer.project_state((self.acquire("2026-09-01", "planned"), self.acquire("2026-09-02", "completed")))
        self.assertEqual("completed", state.values["phase"])
        self.assertFalse(state.conflicts)
        self.assertEqual({"planned", "completed"}, {x.qualifier.value for x in state.observations})

    def test_bond_configuration_compares_currency_and_maturity(self):
        spec = self.registry["issue_bond"]
        first = replace(self.acquire(), frame=spec.frame, predicate=Predicate("issue_bond", "发行债券"),
                        role_bindings=(RoleBinding("issuer", self.a), RoleBinding("security", self.b)),
                        attributes={"currency": {"value": "USD"}, "maturity": {"value": "2030-01-01"}})
        second = replace(first, uuid=uuid4(), attributes={"currency": {"value": "EUR"}, "maturity": {"value": "2030-01-01"}})
        self.assertEqual(MatchDecision.DIFFERENT, self.matcher.match(second, self.matcher.create_canonical(first)).decision)

    def test_missing_state_transition_configuration_never_blindly_overwrites(self):
        spec = replace(self.registry["acquire"], lifecycle=None)
        analyzer = EventAnalyzer(PredicateRegistry({"acquire": spec}))
        state = analyzer.project_state((self.acquire("2026-09-01", "ongoing"), self.acquire("2026-09-02", "completed")))
        self.assertEqual("ongoing", state.values["phase"])
        self.assertTrue(any("未配置" in reason for reason in state.unknown))

    def test_domain_can_explicitly_allow_a_transition_without_engine_changes(self):
        lifecycle = LifecycleSpec({"phase": frozenset({("completed", "ongoing")})})
        spec = replace(self.registry["acquire"], lifecycle=lifecycle)
        analyzer = EventAnalyzer(PredicateRegistry({"acquire": spec}))
        state = analyzer.project_state((self.acquire("2026-09-01", "completed"), self.acquire("2026-09-02", "ongoing")))
        self.assertEqual("ongoing", state.values["phase"])
        self.assertFalse(state.conflicts)

    def test_wrong_acquisition_frame_is_rejected_before_storage(self):
        event = replace(self.acquire(), frame=Frame(Dynamics.STATE, Topology.INTRINSIC, Agency.AGENTIVE))
        with self.assertRaisesRegex(ValueError, "Frame"):
            self.engine.register_event(event)
        self.assertFalse(self.engine.events.data)

    def test_matcher_also_validates_frame_when_input_bypasses_registration(self):
        event = replace(self.acquire(), frame=Frame(Dynamics.CHANGE, Topology.INTRINSIC, Agency.AGENTIVE))
        result = self.matcher.match(event, self.matcher.create_canonical(self.acquire()))
        self.assertEqual(MatchDecision.DIFFERENT, result.decision)
        self.assertTrue(any("Frame" in reason for reason in result.conflicts))

    def test_all_builtin_predicates_have_explicit_structural_constraints(self):
        self.assertTrue(all(spec.frame is not None for spec in self.registry.values()))

    def test_duplicate_resolution_is_idempotent(self):
        event = self.acquire(phase="completed")
        first, _ = self.bind(event)
        again, result = self.engine.resolve_canonical(event.uuid)
        self.assertEqual(first, again)
        self.assertEqual(MatchDecision.DUPLICATE, result.decision)

    def test_cluster_time_union_cannot_bridge_distinct_occurrences(self):
        spec = self.registry["attack"]
        identity = replace(spec.identity, role_weight=.99, time_weight=.01, location_weight=0,
                           auto_merge_threshold=.9)
        # A-C 超过时间窗口，但 B 分别接近 A 和 C；聚合不能靠 B 桥接。
        interval_identity = replace(identity, time_mode="interval")
        matcher = CanonicalEventMatcher(PredicateRegistry({"attack": replace(spec, identity=interval_identity)}))
        first = replace(self.attack(), time={"start_time": time("2026-09-01"), "end_time": time("2026-09-01")})
        second = replace(self.attack(), time={"start_time": time("2026-09-03"), "end_time": time("2026-09-03")})
        seed = matcher.create_canonical(first)
        combined = matcher.update_canonical(seed, (first, second))
        third = replace(self.attack(), time={"start_time": time("2026-09-06"), "end_time": time("2026-09-06")})
        self.assertEqual(MatchDecision.SAME_EVENT, matcher.match(third, combined).decision)
        with self.assertRaisesRegex(ValueError, "成员身份冲突"):
            matcher.update_canonical(combined, (first, second, third))

    def test_registry_rejects_factuality_as_a_lifecycle_dimension(self):
        spec = replace(self.registry["acquire"], lifecycle=LifecycleSpec({"epistemic": frozenset({("denied", "confirmed")})}))
        with self.assertRaisesRegex(ValueError, "事实性"):
            PredicateRegistry({"acquire": spec})


if __name__ == "__main__":
    unittest.main()
