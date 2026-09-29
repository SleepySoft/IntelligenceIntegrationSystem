import unittest
from pathlib import Path

from event_engine.domain.models import SemanticRoleGroup
from event_engine.integration.file_ingestion import ingest_event_file, load_event_file
from event_engine.integration.service import EventEngine
from event_engine.query.memory import InMemoryEventRepository


DATA_FILE = Path(__file__).parents[1] / "examples" / "data" / "multitopic_events.json"


class FileIngestionTests(unittest.TestCase):
    def test_loads_multitopic_file_with_stable_references(self):
        first = load_event_file(DATA_FILE)
        second = load_event_file(DATA_FILE)

        self.assertGreaterEqual(len(first.events), 30)
        self.assertGreaterEqual(len({event.metadata["topic"] for event in first.events}), 30)
        self.assertEqual(
            [event.uuid for event in first.events],
            [event.uuid for event in second.events],
        )
        event_ids = {event.uuid for event in first.events}
        self.assertTrue(all(
            relation.target_event_uuid in event_ids
            for event in first.events
            for relation in event.relations
        ))

    def test_ingests_and_supports_semantic_role_query(self):
        repository = InMemoryEventRepository()
        engine = EventEngine(repository)
        dataset = ingest_event_file(DATA_FILE, engine)
        armed_group = dataset.entities["armed_group"].uuid

        actions = engine.get_entity_actions(armed_group)

        self.assertEqual(1, len(actions))
        self.assertEqual("attack", actions[0].predicate.id)
        self.assertIn(
            armed_group,
            actions[0].entities_for_group(SemanticRoleGroup.AGENT),
        )

    def test_rejects_duplicate_import_before_writing(self):
        repository = InMemoryEventRepository()
        engine = EventEngine(repository)
        dataset = ingest_event_file(DATA_FILE, engine)

        with self.assertRaisesRegex(ValueError, "事件已经存在"):
            ingest_event_file(DATA_FILE, engine)

        self.assertEqual(len(dataset.events), len(repository.data))


if __name__ == "__main__":
    unittest.main()
