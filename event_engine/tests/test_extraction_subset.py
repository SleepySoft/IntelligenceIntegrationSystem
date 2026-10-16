import unittest

from event_engine.domains import registry_for
from event_engine.extraction import (
    CoreExtractionSubset,
    EventExtractionResult,
    complete_core_extraction,
)
from event_engine.extraction.subset import normalize_time_text


def _attack_subset():
    """最小合法核心子集：A军袭击B设施。"""
    return {
        "events": [{
            "is_primary": True,
            "predicate": {"id": "attack", "surface": "袭击"},
            "roles": {
                "actor": [{"name": "A军", "type": "organization", "country_code": None}],
                "target": [{"name": "B设施", "type": "facility"}],
            },
        }],
    }


class CoreSubsetBridgeTests(unittest.TestCase):
    def setUp(self):
        self.registry = registry_for("news", "industry", "financial")

    def complete(self, payload):
        return complete_core_extraction(payload, self.registry)

    def test_completion_assigns_ids_and_derives_frame(self):
        result = self.complete(_attack_subset())
        self.assertIsInstance(result, EventExtractionResult)
        self.assertEqual(result.schema_version, "event-extraction/1.0")
        self.assertEqual(result.primary_event_id, "E1")
        self.assertEqual([entity.id for entity in result.entities], ["ENT1", "ENT2"])
        self.assertEqual(result.entities[0].name, "A军")
        self.assertIsNone(result.entities[1].country_code)
        event = result.events[0]
        self.assertEqual(event.id, "E1")
        registered_frame = self.registry["attack"].frame
        self.assertEqual(event.core.frame.dynamics, registered_frame.dynamics)
        self.assertEqual(event.core.frame.topology, registered_frame.topology)
        self.assertEqual(event.core.frame.agency, registered_frame.agency)
        self.assertEqual(event.core.roles, {"actor": ["ENT1"], "target": ["ENT2"]})

    def test_repeated_mentions_merge_into_one_entity_and_backfill_country(self):
        payload = _attack_subset()
        payload["events"].append({
            "predicate": {"id": "attack", "surface": "再次袭击"},
            "roles": {
                "actor": [{"name": "A军", "type": "organization"}],
                "target": [{"name": " B设施 ", "type": "facility", "country_code": "UA"}],
            },
            "relations": [{"predicate": "follows", "target": 1}],
        })
        # 首次提及缺 country_code，后续提及补全；name 空白差异不影响合并。
        payload["events"][0]["roles"]["target"][0] = {"name": "B设施", "type": "facility"}
        result = self.complete(payload)
        self.assertEqual(len(result.entities), 2)
        self.assertEqual(result.entities[1].country_code, "UA")
        self.assertEqual(result.events[1].core.roles["actor"], ["ENT1"])
        self.assertEqual(result.events[1].core.roles["target"], ["ENT2"])
        self.assertEqual(result.events[1].relations[0].target_event_id, "E1")

    def test_primary_defaults_to_first_event(self):
        payload = _attack_subset()
        del payload["events"][0]["is_primary"]
        result = self.complete(payload)
        self.assertEqual(result.primary_event_id, "E1")

    def test_multiple_primary_markers_are_rejected(self):
        payload = _attack_subset()
        payload["events"].append({
            "is_primary": True,
            "predicate": {"id": "attack", "surface": "袭击"},
            "roles": {"actor": [{"name": "C军", "type": "organization"}]},
        })
        with self.assertRaisesRegex(ValueError, "is_primary"):
            self.complete(payload)

    def test_open_predicate_requires_frame_and_fallback_roles(self):
        payload = _attack_subset()
        event = payload["events"][0]
        event["predicate"] = {"id": None, "surface": "试射", "gloss": "导弹试射"}
        with self.assertRaisesRegex(ValueError, "frame"):
            self.complete(payload)
        event["frame"] = {"dynamics": "process", "topology": "targeted", "agency": "agentive"}
        result = self.complete(payload)
        self.assertIsNone(result.events[0].core.predicate.id)
        self.assertEqual(result.events[0].core.predicate.gloss, "导弹试射")

    def test_agency_hint_only_applies_to_unknown_agency_predicates(self):
        # possess 在 Registry 中 agency=unknown，采纳 LLM 提示。
        payload = _attack_subset()
        event = payload["events"][0]
        event["predicate"] = {"id": "possess", "surface": "拥有"}
        event["roles"] = {"owner": [{"name": "A军", "type": "organization"}],
                          "asset": [{"name": "B设施", "type": "facility"}]}
        event["agency"] = "agentive"
        result = self.complete(payload)
        self.assertEqual(self.registry["possess"].frame.agency.value, "unknown")
        self.assertEqual(result.events[0].core.frame.agency, "agentive")
        # attack 的 agency 已由 Registry 固定，提示被忽略。
        payload = _attack_subset()
        payload["events"][0]["agency"] = "non_agentive"
        result = self.complete(payload)
        self.assertEqual(result.events[0].core.frame.agency,
                         self.registry["attack"].frame.agency)

    def test_registered_predicate_rejects_llm_supplied_frame(self):
        payload = _attack_subset()
        payload["events"][0]["frame"] = {
            "dynamics": "process", "topology": "targeted", "agency": "agentive",
        }
        with self.assertRaisesRegex(ValueError, "frame"):
            self.complete(payload)

    def test_relation_target_bounds_and_self_reference(self):
        payload = _attack_subset()
        payload["events"][0]["relations"] = [{"predicate": "causes", "target": 2}]
        with self.assertRaisesRegex(ValueError, "target"):
            self.complete(payload)
        payload["events"][0]["relations"] = [{"predicate": "causes", "target": 1}]
        with self.assertRaisesRegex(ValueError, "itself"):
            self.complete(payload)

    def test_qualifiers_get_ids_event_scope_and_inline_by(self):
        payload = _attack_subset()
        payload["events"][0]["qualifiers"] = [
            {"type": "directive", "value": "ordered",
             "by": [{"name": "某领导人", "type": "person", "country_code": "IL"}]},
            {"type": "epistemic", "value": "asserted",
             "by": [{"name": "某领导人", "type": "person"}]},
        ]
        result = self.complete(payload)
        qualifiers = result.events[0].qualifiers
        self.assertEqual([q.id for q in qualifiers], ["Q1", "Q2"])
        self.assertTrue(all(q.scope == "event" for q in qualifiers))
        # 两处 by 提及合并为同一实体。
        self.assertEqual(qualifiers[0].by, qualifiers[1].by)
        self.assertEqual(result.entities[-1].name, "某领导人")

    def test_epistemic_qualifier_requires_by(self):
        payload = _attack_subset()
        payload["events"][0]["qualifiers"] = [{"type": "epistemic", "value": "asserted"}]
        with self.assertRaisesRegex(ValueError, "by is required"):
            self.complete(payload)

    def test_time_format_is_repaired_or_dropped(self):
        payload = _attack_subset()
        payload["events"][0]["time"] = {
            "event_time": {
                "normalized": "2026-10-12 14:30:45+08:00",
                "precision": "minute",
                "approximate": False,
                "surface": "12日下午2点半",
            },
        }
        result = self.complete(payload)
        self.assertEqual(result.events[0].core.time.event_time.normalized, "2026-10-12T14:30")
        # 日历上不存在的日期降级为 null，而不是整次校验失败。
        payload["events"][0]["time"]["event_time"]["normalized"] = "2026-13-40T25:61"
        result = self.complete(payload)
        self.assertIsNone(result.events[0].core.time.event_time.normalized)

    def test_normalize_time_text_unit(self):
        self.assertEqual(normalize_time_text("2026-10-12", "day"), "2026-10-12")
        self.assertEqual(normalize_time_text("2026-10-12T14:30:45", "minute"),
                         "2026-10-12T14:30")
        self.assertEqual(normalize_time_text("2026-10-12T14:30Z", "minute"),
                         "2026-10-12T14:30")
        self.assertEqual(normalize_time_text("2026-10-12T14:30", "hour"),
                         "2026-10-12T14")
        self.assertIsNone(normalize_time_text("2026-13-01", "day"))
        self.assertIsNone(normalize_time_text("2026-10", "day"))
        self.assertIsNone(normalize_time_text("下个月", "day"))

    def test_subset_model_rejects_unknown_fields_and_empty_events(self):
        with self.assertRaises(ValueError):
            CoreExtractionSubset.model_validate({"events": []})
        payload = _attack_subset()
        payload["schema_version"] = "event-extraction/1.0"
        with self.assertRaises(ValueError):
            CoreExtractionSubset.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
