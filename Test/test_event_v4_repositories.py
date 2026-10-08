from datetime import datetime, timezone
from uuid import UUID

from ServiceComponent.IntelligenceHubDefines_v4 import (
    DEFAULT_EVENT_REGISTRY,
    validate_analysis_result_v4,
)
from ServiceComponent.event_v4_archive import ArchivedIntelligenceV4
from ServiceComponent.event_v4_conversion import (
    DeterministicEntityResolver,
    ResolvedEntity,
    convert_event_extraction,
)
from ServiceComponent.event_v4_repositories import (
    MongoEntityRepository,
    MongoEventV4ArchiveRepository,
)


class FakeCursor(list):
    def sort(self, *args, **kwargs):
        return self

    def limit(self, count):
        return FakeCursor(self[:count])


class FakeCollection:
    def __init__(self):
        self.documents = {}
        self.indexes = []

    def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))

    def find_one(self, query, projection=None):
        for document in self.documents.values():
            if _matches(document, query):
                if projection:
                    return {key: document[key] for key in projection if key in document}
                return dict(document)
        return None

    def find(self, query):
        return FakeCursor(dict(value) for value in self.documents.values() if _matches(value, query))

    def update_one(self, query, update, upsert=False):
        current = self.find_one(query)
        if current is None:
            if not upsert:
                return
            current = dict(query) if "$or" not in query else {}
        for key, value in update.get("$setOnInsert", {}).items():
            current.setdefault(key, value)
        current.update(update.get("$set", {}))
        for key, value in update.get("$addToSet", {}).items():
            values = current.setdefault(key, [])
            if value not in values:
                values.append(value)
        self.documents[str(current["_id"])] = current

    def replace_one(self, query, document, upsert=False):
        assert upsert or self.find_one(query) is not None
        self.documents[str(document["_id"])] = dict(document)

    def bulk_write(self, operations, ordered=True):
        for operation in operations:
            self.replace_one(operation._filter, operation._doc, upsert=operation._upsert)


class FailOnceCollection(FakeCollection):
    def __init__(self):
        super().__init__()
        self.should_fail = True

    def replace_one(self, query, document, upsert=False):
        if self.should_fail:
            self.should_fail = False
            raise RuntimeError("simulated interrupted archive write")
        return super().replace_one(query, document, upsert=upsert)


def _matches(document, query):
    if "$or" in query:
        return any(_matches(document, item) for item in query["$or"])
    return all(document.get(key) == value for key, value in query.items())


def _analysis_payload():
    return {
        "kind": "valuable",
        "message": {"title": "A军袭击B设施", "brief": "A军袭击B设施。", "text": "详细简报。"},
        "classification": {"taxonomy": "政治与安全", "subcategories": ["国防军事"]},
        "assessment": {
            "impact": "造成安全影响。", "reason": "存在明确军事行动。", "tips": "",
            "rate": {"影响广度": 4, "影响深度": 5, "新颖性与异常性": 6, "演化与连锁潜力": 5, "舆情及认知影响": 3, "可行动性": 6},
        },
        "event_extraction": {
            "schema_version": "event-extraction/1.0", "primary_event_id": "E1",
            "entities": [
                {"id": "ENT1", "name": "A军", "type": "organization"},
                {"id": "ENT2", "name": "B设施", "type": "facility"},
            ],
            "events": [{"id": "E1", "core": {
                "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
                "predicate": {"id": "attack", "surface": "袭击"},
                "roles": {"actor": ["ENT1"], "target": ["ENT2"]},
            }}],
        },
    }


def _archive_and_events():
    analysis = validate_analysis_result_v4(_analysis_payload())
    intelligence_uuid = UUID("30eb0459-998d-486f-8eac-470b9dfd98ec")
    now = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
    conversion = convert_event_extraction(
        analysis.event_extraction,
        intelligence_uuid=intelligence_uuid,
        entity_resolver=DeterministicEntityResolver(),
        registry=DEFAULT_EVENT_REGISTRY,
        observed_at=now,
    )
    archive = ArchivedIntelligenceV4(
        intelligence_uuid=intelligence_uuid,
        informant="https://example.test/v4",
        raw_data={"UUID": str(intelligence_uuid), "content": "raw"},
        analysis=analysis,
        ai_service="fake://ai",
        ai_model="fake-v4",
        prompt_version=40,
        processed_at=now,
        archived_at=now,
        total_score=6.5,
        event_uuids=[event.uuid for event in conversion.events],
        primary_event_uuid=conversion.primary_event_uuid,
        subsystem="news",
    )
    return archive, conversion.events


def test_entity_repository_upsert_preserves_one_global_identity_and_aliases():
    collection = FakeCollection()
    repository = MongoEntityRepository(collection)
    repository.ensure_indexes()
    entity = ResolvedEntity(
        uuid=UUID("22cb75ca-c7e3-5e85-b3e7-46579d8b69c1"),
        identity_key="organization|CN|示例机构",
        canonical_name="示例机构",
        entity_type="organization",
        country_code="CN",
    )
    first = repository.upsert(entity, alias="示例机构")
    second = repository.upsert(entity, alias="示例 机构")
    assert first.uuid == second.uuid == entity.uuid
    assert len(collection.documents) == 1
    assert collection.documents[str(entity.uuid)]["aliases"] == ["示例机构", "示例 机构"]


def test_archive_repository_is_idempotent_and_commits_events_before_intelligence():
    intelligence = FakeCollection()
    low_value = FakeCollection()
    events = FakeCollection()
    outbox = FakeCollection()
    repository = MongoEventV4ArchiveRepository(
        intelligence_collection=intelligence,
        low_value_collection=low_value,
        event_collection=events,
        outbox_collection=outbox,
    )
    repository.ensure_indexes()
    archive, records = _archive_and_events()

    repository.commit(archive, records)
    repository.commit(archive, records)

    assert len(intelligence.documents) == 1
    assert len(events.documents) == 1
    assert outbox.documents[str(archive.intelligence_uuid)]["status"] == "committed"
    assert repository.contains(archive.intelligence_uuid, archive.informant)
    assert isinstance(intelligence.documents[str(archive.intelligence_uuid)]["archived_at"], datetime)


def test_pending_outbox_can_be_recovered_after_interrupted_commit():
    intelligence = FailOnceCollection()
    events = FakeCollection()
    outbox = FakeCollection()
    repository = MongoEventV4ArchiveRepository(
        intelligence_collection=intelligence,
        low_value_collection=FakeCollection(),
        event_collection=events,
        outbox_collection=outbox,
    )
    archive, records = _archive_and_events()
    import pytest

    with pytest.raises(RuntimeError, match="interrupted"):
        repository.commit(archive, records)
    assert outbox.documents[str(archive.intelligence_uuid)]["status"] == "pending"
    assert len(events.documents) == 1
    assert not intelligence.documents
    assert repository.recover_pending() == 1
    assert len(events.documents) == 1
    assert len(intelligence.documents) == 1
    assert outbox.documents[str(archive.intelligence_uuid)]["status"] == "committed"
