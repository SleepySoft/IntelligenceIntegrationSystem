from copy import deepcopy

import pytest

from EntityResolution import EntityRef, EntityResolutionService, ResolutionError, ResolutionScope


class MemoryStore:
    def __init__(self):
        self.assignments = {}
        self.contexts = {}
        self.audits = {}

    def resolve_many(self, refs):
        result = {}
        for ref in refs:
            value = self.assignments.get(ref.key)
            if not value:
                continue
            item = deepcopy(value)
            if item["kind"] == "contextual":
                context = self.contexts[item["target_id"]]
                item.update(name=context["name"], type=context.get("type", "other"))
            result[ref.key] = item
        return result

    def get_assignment(self, ref):
        return deepcopy(self.assignments.get(ref.key))

    def apply_assignment(self, ref, assignment):
        if assignment is None:
            self.assignments.pop(ref.key, None)
        else:
            self.assignments[ref.key] = deepcopy(assignment)

    def create_contextual(self, document):
        self.contexts[document["id"]] = deepcopy(document)
        return deepcopy(document)

    def get_contextual(self, entity_id):
        return deepcopy(self.contexts.get(entity_id))

    def search_contextual(self, query, limit=20):
        return [deepcopy(item) for item in self.contexts.values()
                if query.casefold() in item["name"].casefold()][:limit]

    def insert_audit(self, document):
        self.audits[document["decision_id"]] = deepcopy(document)

    def get_audit(self, decision_id):
        return deepcopy(self.audits.get(decision_id))

    def mark_undone(self, decision_id, actor):
        self.audits[decision_id]["undone_at"] = "now"
        self.audits[decision_id]["undone_by"] = actor

    def list_references(self, target_id, limit=200):
        return [value for value in self.assignments.values()
                if value.get("target_id") == target_id][:limit]


class Registry:
    entities = {
        "ent_jfk": {"id": "ent_jfk", "canonical": "约翰·肯尼迪", "type": "person"},
    }

    def get_entity(self, entity_id):
        return deepcopy(self.entities.get(entity_id))

    def resolve(self, name):
        if name == "约翰·肯尼迪":
            return {"entity_id": "ent_jfk", "canonical": name,
                    "type": "person", "status": "verified"}
        return None


def make_service():
    store = MemoryStore()
    entities = {
        "ENT1": {"id": "ENT1", "name": "肯尼迪", "type": "person"},
        "ENT2": {"id": "ENT2", "name": "示威者", "type": "organization"},
    }

    def load(ref):
        return deepcopy(entities.get(ref.local_entity_id))

    service = EntityResolutionService(
        {"news": ResolutionScope(store, load)}, entity_registry=Registry())
    return service, store


def test_entity_ref_is_stable_and_validated():
    ref = EntityRef("news", "article-a", "ENT1")
    assert ref.key == "news:article-a:ENT1"
    with pytest.raises(ResolutionError):
        EntityRef("news", "article-a", "person-1")


def test_assigns_ambiguous_name_to_global_entity_without_registering_alias():
    service, _ = make_service()
    ref = EntityRef("news", "article-a", "ENT1")

    result = service.assign_global(ref, "ent_jfk", actor="alice")
    resolved = service.resolve_many([{
        **ref.to_dict(), "surface": "肯尼迪", "type": "person",
    }])[ref.key]

    assert result["changed"] is True
    assert resolved["target_id"] == "ent_jfk"
    assert resolved["name"] == "约翰·肯尼迪"
    assert Registry().resolve("肯尼迪") is None


def test_contextual_entity_only_requires_name_and_can_collect_multiple_refs():
    service, _ = make_service()
    context = service.create_contextual("news", "法国示威者", actor="alice")
    first = EntityRef("news", "article-a", "ENT2")
    second = EntityRef("news", "article-b", "ENT2")

    service.assign_contextual(first, context["id"], actor="alice")
    service.assign_contextual(second, context["id"], actor="alice")
    resolved = service.resolve_many([
        {**first.to_dict(), "surface": "示威者"},
        {**second.to_dict(), "surface": "抗议者"},
    ])

    assert context["name"] == "法国示威者"
    assert {value["target_id"] for value in resolved.values()} == {context["id"]}
    assert {value["name"] for value in resolved.values()} == {"法国示威者"}


def test_reassignment_and_undo_restore_previous_target():
    service, _ = make_service()
    ref = EntityRef("news", "article-a", "ENT1")
    first = service.create_contextual("news", "第一组", actor="alice")
    second = service.create_contextual("news", "第二组", actor="alice")
    service.assign_contextual(ref, first["id"], actor="alice")
    changed = service.assign_contextual(ref, second["id"], actor="alice")

    service.undo("news", changed["decision_id"], actor="alice")

    resolved = service.resolve_many([{**ref.to_dict(), "surface": "肯尼迪"}])
    assert resolved[ref.key]["target_id"] == first["id"]


def test_invalid_reference_and_target_are_rejected():
    service, _ = make_service()
    with pytest.raises(ResolutionError) as missing_ref:
        service.assign_global(
            EntityRef("news", "article-a", "ENT9"), "ent_jfk", actor="alice")
    assert missing_ref.value.code == "not_found"

    with pytest.raises(ResolutionError) as missing_target:
        service.assign_global(
            EntityRef("news", "article-a", "ENT1"), "ent_missing", actor="alice")
    assert missing_target.value.code == "not_found"
