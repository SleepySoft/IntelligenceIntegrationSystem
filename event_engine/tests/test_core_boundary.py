import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from event_engine.core import EventAnalyzer, EventEngine, PredicateRegistry
from event_engine.schema import (
    ArgumentRoleSpec, DomainPack, EventRecord, IdentitySpec, MatchDecision, PredicateSpec,
)
from event_engine.schema import (
    Agency, Dynamics, EventIR, Frame, Predicate, Qualifier, RoleBinding,
    SemanticRoleGroup, TimeExpression, Topology,
)
from event_engine.integration.file_ingestion import ingest_event_file
from event_engine.query.memory import InMemoryCanonicalEventRepository, InMemoryEventRepository
from event_engine.query.serialization import event_from_document, event_to_document


class CoreBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.entity = uuid4()
        self.spec = PredicateSpec(
            "custom:action", {"initiator": SemanticRoleGroup.AGENT},
            IdentitySpec(("initiator",), role_weight=1, time_weight=0,
                         location_weight=0, attribute_weight=0, lifecycle_weight=0),
            arguments=ArgumentRoleSpec(frozenset({"initiator"})),
            required_roles=("initiator",),
        )
        self.registry = PredicateRegistry.from_packs(DomainPack("custom", "1", {self.spec.predicate_id: self.spec}))
        self.ir = EventIR(
            Frame(Dynamics.PROCESS, Topology.INTRINSIC, Agency.AGENTIVE),
            Predicate("custom:action", "自定义动作"),
            (RoleBinding("initiator", self.entity),),
        )

    def record(self, ir=None):
        return EventRecord.from_ir(ir or self.ir, uuid=uuid4(), intelligence_uuid=uuid4(),
                                   local_event_id="E1", observed_at=datetime.now(timezone.utc))

    def test_core_import_does_not_load_domain_configuration_or_storage(self):
        code = (
            "import sys; import event_engine.core; "
            "assert not any(n.startswith(('event_engine.domains', 'event_engine.configs', 'event_engine.extensions', "
            "'event_engine.domain', 'event_engine.query')) for n in sys.modules)"
        )
        subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)

    def test_ir_needs_no_source_and_roundtrips_through_record_and_storage(self):
        time = TimeExpression("2026-09-30", "day", False, "9月30日")
        ir = replace(self.ir, qualifiers=(Qualifier("Q1", "phase", "ongoing", by=(self.entity,), time=time),))
        record = self.record(ir)
        self.assertEqual(ir, record.ir)
        self.assertEqual(record, event_from_document(event_to_document(record)))
        self.assertFalse(hasattr(ir, "intelligence_uuid"))
        self.assertEqual((self.entity,), EventAnalyzer(self.registry).participants(ir))

    def test_schema_import_does_not_load_execution_or_adapters(self):
        code = (
            "import sys; import event_engine.schema; "
            "from event_engine.schema import EventRecord, CanonicalEvent, LifecycleSpec, "
            "DomainPack, EventQuery, EventRepository; "
            "assert not any(n.startswith(('event_engine.core', 'event_engine.domains', 'event_engine.configs', "
            "'event_engine.extensions', 'event_engine.integration', 'event_engine.query')) "
            "for n in sys.modules); "
            "assert not hasattr(LifecycleSpec(), 'allows')"
        )
        subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)

    def test_unseen_vocabulary_drives_roles_queries_and_matching(self):
        engine = EventEngine(InMemoryEventRepository(), InMemoryCanonicalEventRepository(), registry=self.registry)
        first, second = self.record(), self.record()
        engine.register_event(first)
        engine.register_event(second)
        self.assertIs(engine.registry, engine.analyzer.specs)
        self.assertIs(engine.registry, engine.matcher.specs)
        self.assertEqual({first.uuid, second.uuid}, {e.uuid for e in engine.get_entity_actions(self.entity)})
        self.assertEqual("initiator", engine.classify_event_roles(first.uuid).subjects[0].role)
        canonical, _ = engine.resolve_canonical(first.uuid)
        updated, result = engine.resolve_canonical(second.uuid)
        self.assertEqual(canonical.uuid, updated.uuid)
        self.assertEqual(MatchDecision.SAME_EVENT, result.decision)

    def test_explicit_empty_registry_does_not_load_defaults(self):
        from event_engine.integration.service import EventEngine as CompatibleEngine
        registry = PredicateRegistry()
        engine = CompatibleEngine(InMemoryEventRepository(), registry=registry)
        self.assertIs(registry, engine.registry)
        self.assertFalse(engine.analyzer.specs)
        self.assertFalse(engine.matcher.specs)
        self.assertFalse(EventEngine(InMemoryEventRepository()).registry)

    def test_registry_rejects_collisions_and_freezes_role_maps(self):
        pack = DomainPack("other", "1", {self.spec.predicate_id: self.spec})
        with self.assertRaisesRegex(ValueError, "重复谓词"):
            PredicateRegistry.from_packs(DomainPack("custom", "1", dict(self.registry)), pack)
        roles = {"initiator": SemanticRoleGroup.AGENT}
        registry = PredicateRegistry({self.spec.predicate_id: replace(self.spec, role_groups=roles)})
        roles["initiator"] = SemanticRoleGroup.AFFECTED
        self.assertEqual(SemanticRoleGroup.AGENT, registry[self.spec.predicate_id].role_groups["initiator"])
        with self.assertRaises(TypeError):
            registry[self.spec.predicate_id].role_groups["initiator"] = SemanticRoleGroup.OTHER

    def test_declared_constraints_apply_before_registration(self):
        engine = EventEngine(InMemoryEventRepository(), registry=self.registry)
        with self.assertRaisesRegex(ValueError, "必填角色"):
            engine.register_event(self.record(replace(self.ir, role_bindings=())))
        self.assertFalse(engine.events.data)

    def test_file_ingestion_uses_engine_registry_and_validates_before_writes(self):
        payload = {
            "schema_version": "event-file/1.0", "dataset_id": "custom-registry",
            "entities": [{"id": "A", "name": "A", "type": "organization"}],
            "events": [{
                "id": "E1", "frame": {"dynamics": "process", "topology": "intrinsic", "agency": "agentive"},
                "predicate": {"id": "custom:action", "surface": "动作"},
                "roles": {"initiator": ["A"]}, "observed_at": "2026-09-30T00:00:00Z",
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            engine = EventEngine(InMemoryEventRepository(), registry=self.registry)
            dataset = ingest_event_file(path, engine)
            entity = dataset.entities["A"].uuid
            self.assertEqual((dataset.events[0],), engine.get_entity_actions(entity))
            bad = dict(payload["events"][0], id="E2", roles={"unknown": ["A"]})
            payload["events"].append(bad)
            path.write_text(json.dumps(payload), encoding="utf-8")
            empty_engine = EventEngine(InMemoryEventRepository(), registry=self.registry)
            with self.assertRaisesRegex(ValueError, "必填角色"):
                ingest_event_file(path, empty_engine)
            self.assertFalse(empty_engine.events.data)

    def test_builtin_packs_compose_without_duplicate_definitions(self):
        from event_engine.domains.news import PACK as NEWS_PACK
        from event_engine.domains.industry import PACK as INDUSTRY_PACK
        from event_engine.domains.financial import PACK as FINANCIAL_PACK
        registry = PredicateRegistry.from_packs(NEWS_PACK, INDUSTRY_PACK, FINANCIAL_PACK)
        self.assertEqual({"news": "1.1", "industry": "1.1", "financial": "1.1"}, dict(registry.pack_versions))
        for predicate in ("attack", "build_facility", "issue_bond"):
            self.assertIn(predicate, registry)
        self.assertEqual(SemanticRoleGroup.AGENT, registry["issue_bond"].role_groups["issuer"])


if __name__ == "__main__":
    unittest.main()
