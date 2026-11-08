"""EntityAlias 归档后自动增长测试（不访问真实网络）。"""

import json

import pytest

from EntityAlias import EntityAliasService, JSONLStore
from EntityAlias.model import AliasError
from EntityAlias.extension import EntityAliasAutoEnrichmentExtension
from ServiceComponent.pipeline import ARCHIVE_COMPLETED
from ServiceComponent.runtime import HubEvent, HubRuntime


class FakeSuggester:
    def __init__(self, candidates):
        self.candidates = candidates

    def suggest(self, name, *, lang="zh", limit=10):
        del name, lang, limit
        return list(self.candidates)

    def prefill(self, qid, *, primary_lang="zh"):
        return {
            "canonical": "纽约时报",
            "lang": primary_lang,
            "localized": {"en": "The New York Times"},
            "aliases": [{"name": "NYT", "lang": "en", "role": "abbr"}],
            "description": "美国报纸",
            "refs": {"wikidata": qid},
        }


class FakeArchiveCollection:
    def __init__(self, documents):
        self.documents = documents

    def find(self, query, projection):
        del query, projection
        return iter(self.documents)


def _archive(name="纽约时报", entity_type="organization"):
    return {
        "EVENT_EXTRACTION": {
            "entities": [{"id": "ENT1", "name": name, "type": entity_type}],
        },
    }


def test_auto_register_exact_enriches_aliases_and_fixes_type(tmp_path):
    suggester = FakeSuggester([{
        "qid": "Q9684", "label": "纽约时报", "matched": "纽约时报",
    }])
    service = EntityAliasService(JSONLStore(str(tmp_path)), suggester=suggester)

    result = service.auto_register_exact("纽约时报", entity_type="organization")

    assert result["status"] == "registered"
    assert service.resolve("NYT")["canonical"] == "纽约时报"
    assert service.resolve("纽约时报")["type"] == "organization"
    assert "auto_enrichment" not in service.status()


def test_english_search_keeps_chinese_as_primary_language(tmp_path):
    suggester = FakeSuggester([{
        "qid": "Q9684", "label": "The New York Times",
        "matched": "The New York Times",
    }])
    service = EntityAliasService(JSONLStore(str(tmp_path)), suggester=suggester)

    result = service.auto_register_exact(
        "The New York Times", entity_type="organization", search_lang="en")

    assert result["status"] == "registered"
    assert result["entity"]["lang"] == "zh"


def test_auto_register_exact_refuses_ambiguous_same_name(tmp_path):
    suggester = FakeSuggester([
        {"qid": "Q1", "label": "同名实体", "matched": "同名实体"},
        {"qid": "Q2", "label": "同名实体", "matched": "同名实体"},
    ])
    service = EntityAliasService(JSONLStore(str(tmp_path)), suggester=suggester)

    result = service.auto_register_exact("同名实体", entity_type="person")

    assert result["status"] == "ambiguous"
    assert service.resolve("同名实体") is None


def test_archive_extension_processes_unknown_names_off_runtime_thread(tmp_path):
    suggester = FakeSuggester([{
        "qid": "Q9684", "label": "纽约时报", "matched": "纽约时报",
    }])
    service = EntityAliasService(JSONLStore(str(tmp_path)), suggester=suggester)
    extension = EntityAliasAutoEnrichmentExtension(service)
    runtime = HubRuntime()
    runtime.install(extension)
    runtime.start()
    try:
        runtime.emit(HubEvent(ARCHIVE_COMPLETED, _archive(), "news"))
        assert runtime.wait_for_idle(timeout=2.0)
        assert extension.wait_for_idle(timeout=2.0)
        assert service.resolve("纽约时报")["entity_id"].startswith("ent_")
        assert extension.statistics["registered"] == 1
    finally:
        runtime.stop()


def test_service_rejects_cross_type_duplicate_names_from_jsonl(tmp_path):
    common = {
        "lang": "zh", "aliases": [], "localized": {}, "native_lang": "",
        "description": "", "status": "unverified", "refs": {},
        "created_at": "", "updated_at": "",
    }
    (tmp_path / "person.jsonl").write_text(json.dumps({
        **common, "id": "ent_person", "canonical": "同名实体", "type": "person",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    (tmp_path / "organization.jsonl").write_text(json.dumps({
        **common, "id": "ent_org", "canonical": "同名实体", "type": "organization",
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(AliasError, match="同时属于"):
        EntityAliasService(JSONLStore(str(tmp_path)))


def test_startup_backfill_queues_historical_names(tmp_path):
    suggester = FakeSuggester([{
        "qid": "Q9684", "label": "纽约时报", "matched": "纽约时报",
    }])
    service = EntityAliasService(JSONLStore(str(tmp_path)), suggester=suggester)
    extension = EntityAliasAutoEnrichmentExtension(
        service, archive_collections=[FakeArchiveCollection([_archive()])])
    runtime = HubRuntime()
    runtime.install(extension)
    runtime.start()
    try:
        assert extension._backfiller is not None
        extension._backfiller.join(timeout=2.0)
        assert extension.wait_for_idle(timeout=2.0)
        assert service.resolve("纽约时报") is not None
    finally:
        runtime.stop()
