from datetime import datetime, timezone
from uuid import UUID

from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY, validate_analysis_result_v4
from ServiceComponent.event_v4_archive import ArchivedDataV4, build_archive_appendix
from ServiceComponent.event_v4_conversion import convert_event_extraction
from ServiceComponent.event_v4_repositories import MongoEventV4ArchiveRepository


class FakeCursor(list):
    def sort(self, *args, **kwargs): return self
    def limit(self, count): return FakeCursor(self[:count])


class FakeCollection:
    def __init__(self, *, fail_bulk_once=False, fail_update_once=False):
        self.documents, self.indexes = [], []
        self.fail_bulk_once = fail_bulk_once
        self.fail_update_once = fail_update_once

    def create_index(self, keys, **kwargs): self.indexes.append((keys, kwargs))
    def find_one(self, query, projection=None):
        return next((dict(item) for item in self.documents if _matches(item, query)), None)
    def find(self, query):
        return FakeCursor(dict(item) for item in self.documents if _matches(item, query))
    def replace_one(self, query, document, upsert=False):
        index = next((i for i, item in enumerate(self.documents) if _matches(item, query)), None)
        value = dict(document)
        if index is None:
            value.setdefault("_id", f"mongo-{len(self.documents) + 1}")
            self.documents.append(value)
        else:
            value.setdefault("_id", self.documents[index].get("_id"))
            self.documents[index] = value
    def update_one(self, query, update, upsert=False):
        if self.fail_update_once:
            self.fail_update_once = False
            raise RuntimeError("simulated outbox failure")
        current = next((item for item in self.documents if _matches(item, query)), None)
        if current is None:
            if not upsert: return
            current = {key: value for key, value in query.items() if not key.startswith("$")}
            current["_id"] = f"mongo-{len(self.documents) + 1}"
            self.documents.append(current)
        for key, value in update.get("$setOnInsert", {}).items(): current.setdefault(key, value)
        current.update(update.get("$set", {}))
    def bulk_write(self, operations, ordered=True):
        if self.fail_bulk_once:
            self.fail_bulk_once = False
            raise RuntimeError("simulated event-index failure")
        for operation in operations:
            self.replace_one(operation._filter, operation._doc, upsert=operation._upsert)


def _matches(document, query):
    return all(document.get(key) == value for key, value in query.items())


def _archive_and_events():
    payload = {
        "TAXONOMY": "政治与安全", "SUB_CATEGORY": ["国防军事"], "REASON": "存在明确军事行动。",
        "EVENT_TITLE": "A军袭击B设施", "EVENT_BRIEF": "A军袭击B设施。", "EVENT_TEXT": "详细简报。",
        "IMPACT": "造成安全影响。", "TIPS": "",
        "RATE": {"影响广度": 4, "影响深度": 5, "新颖性与异常性": 6, "演化与连锁潜力": 5,
                 "舆情及认知影响": 3, "可行动性": 6},
        "EVENT_EXTRACTION": {"schema_version": "event-extraction/1.0", "primary_event_id": "E1",
            "entities": [{"id": "ENT1", "name": "A军", "type": "organization"},
                         {"id": "ENT2", "name": "B设施", "type": "facility"}],
            "events": [{"id": "E1", "core": {
                "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
                "predicate": {"id": "attack", "surface": "袭击"},
                "roles": {"actor": ["ENT1"], "target": ["ENT2"]}}}]},
    }
    analysis = validate_analysis_result_v4(payload)
    source_uuid = UUID("30eb0459-998d-486f-8eac-470b9dfd98ec")
    now = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
    conversion = convert_event_extraction(analysis.EVENT_EXTRACTION, intelligence_uuid=source_uuid,
                                          registry=DEFAULT_EVENT_REGISTRY, observed_at=now)
    appendix = build_archive_appendix({"content": "raw"}, subsystem="news", ai_service="fake://ai",
                                      ai_model="fake-v4", prompt_version=40, processed_at=now,
                                      archived_at=now, total_score=6.5)
    archive = ArchivedDataV4(**analysis.model_dump(mode="python"), UUID=str(source_uuid),
                             INFORMANT="https://example.test/v4", APPENDIX=appendix)
    return archive, conversion.events


def _repository(events=None, outbox=None):
    return MongoEventV4ArchiveRepository(
        intelligence_collection=FakeCollection(), low_value_collection=FakeCollection(),
        event_collection=events or FakeCollection(), outbox_collection=outbox or FakeCollection())


def test_archive_is_idempotent_and_authoritative():
    repository = _repository()
    repository.ensure_indexes()
    archive, records = _archive_and_events()
    repository.commit(archive, records)
    repository.commit(archive, records)
    assert len(repository.intelligence_collection.documents) == 1
    assert len(repository.event_collection.documents) == 1
    stored = repository.intelligence_collection.documents[0]
    assert stored["UUID"] == archive.UUID
    assert stored["EVENT_EXTRACTION"]["primary_event_id"] == "E1"
    assert "analysis" not in stored and "raw_data" not in stored
    assert repository.contains_source(archive.INFORMANT) and repository.contains_id(archive.UUID)


def test_event_index_failure_does_not_rollback_archive_and_can_recover():
    events = FakeCollection(fail_bulk_once=True)
    repository = _repository(events)
    archive, records = _archive_and_events()
    repository.commit(archive, records)
    assert len(repository.intelligence_collection.documents) == 1
    assert repository.outbox_collection.documents[0]["status"] == "pending"
    assert repository.recover_pending() == 1
    assert repository.outbox_collection.documents[0]["status"] == "committed"
    assert events.documents[0]["roles"]["actor"] == [{"name": "A军", "type": "organization"}]
    assert "entity_uuid" not in str(events.documents[0])


def test_outbox_failure_does_not_fail_authoritative_archive():
    repository = _repository(outbox=FakeCollection(fail_update_once=True))
    archive, records = _archive_and_events()

    repository.commit(archive, records)

    assert len(repository.intelligence_collection.documents) == 1
    assert repository.intelligence_collection.documents[0]["UUID"] == archive.UUID
    assert not repository.event_collection.documents
