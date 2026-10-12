import unittest

from event_engine.domains import registry_for
from event_engine.extraction import (
    build_event_extraction_prompt,
    build_predicate_catalog,
    event_extraction_json_schema,
    validate_event_extraction,
)


class ExtractionContractTests(unittest.TestCase):
    def setUp(self):
        self.registry = registry_for("news", "industry", "financial")
        self.payload = {
            "schema_version": "event-extraction/1.0",
            "primary_event_id": "E1",
            "entities": [
                {"id": "ENT1", "name": "甲方", "type": "organization"},
                {"id": "ENT2", "name": "乙方", "type": "organization"},
            ],
            "events": [{
                "id": "E1",
                "core": {
                    "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
                    "predicate": {"id": "attack", "surface": "攻击"},
                    "roles": {"actor": ["ENT1"], "target": ["ENT2"]},
                },
            }],
        }

    def test_standalone_prompt_and_schema_use_selected_registry(self):
        catalog = build_predicate_catalog(self.registry)
        prompt = build_event_extraction_prompt(self.registry)
        schema = event_extraction_json_schema(self.registry)
        self.assertEqual(len(self.registry), len(catalog.splitlines()))
        self.assertIn(catalog, prompt)
        self.assertIn("{{CONTENT}}", prompt)
        self.assertIn("乌克兰的发电厂", prompt)
        self.assertIn("country_code=UA", prompt)
        self.assertIn("country_code 不表示所有权", prompt)
        enum = schema["$defs"]["ExtractedPredicate"]["properties"]["id"]["anyOf"][0]["enum"]
        self.assertEqual(list(self.registry), enum)
        country_schema = schema["$defs"]["ExtractedEntity"]["properties"]["country_code"]
        self.assertIn("不表示所有权", country_schema["description"])

    def test_semantics_are_validated_against_registry(self):
        result = validate_event_extraction(self.payload, self.registry)
        self.assertEqual("E1", result.primary_event_id)
        self.payload["events"][0]["core"]["frame"]["topology"] = "relational"
        with self.assertRaisesRegex(ValueError, "frame does not match"):
            validate_event_extraction(self.payload, self.registry)

    def test_engine_only_predicates_are_exposed_to_extractors(self):
        catalog = build_predicate_catalog(self.registry)
        self.assertIn("build_facility", catalog)
        self.assertIn("declare_dividend", catalog)
        self.assertTrue(self.registry["build_facility"].label)
        self.assertTrue(self.registry["build_facility"].definition)


if __name__ == "__main__":
    unittest.main()
