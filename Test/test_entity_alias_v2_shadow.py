"""EntityAlias V2 影子：资源隔离、观测持久化和保守候选增长。"""

import sqlite3

from EntityAliasV2 import EntityAliasV2Service, SQLiteAliasV2Store
from EntityAliasV2.iis_integration import create_entity_alias_v2_shadow
from EntityAliasV2.shadow import (
    EntityAliasV2ShadowExtension,
    EntityAliasV2ShadowStore,
)
from Scripts.entity_alias_v2_shadow_report import build_report


class FakeSuggester:
    def suggest(self, name, *, lang="zh", limit=5):
        return [{
            "qid": "QWHITEHOUSE",
            "label": name,
            "matched": name,
            "lang": lang,
        }]

    def prefill(self, qid, *, primary_lang="zh"):
        assert qid == "QWHITEHOUSE"
        return {
            "canonical": "白宫",
            "lang": "zh",
            "localized": {"en": "White House"},
            "aliases": [{"name": "美国白宫", "lang": "zh"}],
            "description": "美国总统官邸及相关称谓",
            "refs": {"wikidata": qid},
        }


class FakeV1:
    def resolve(self, name):
        return None


def _archive(entity_type="facility"):
    return {
        "UUID": "article-1",
        "APPENDIX": {"__DOMAIN_NAME__": "news"},
        "EVENT_EXTRACTION": {
            "entities": [{
                "id": "ENT1", "name": "白宫", "type": entity_type,
                "country_code": "US",
            }],
            "events": [],
        },
    }


def test_shadow_observes_without_mutating_archive(tmp_path):
    facts = SQLiteAliasV2Store(str(tmp_path / "facts.db"))
    service = EntityAliasV2Service(facts)
    observations = EntityAliasV2ShadowStore(str(tmp_path / "observations.db"))
    extension = EntityAliasV2ShadowExtension(
        service, observations, v1_service=FakeV1(), suggester=None)
    document = _archive()

    assert extension.observe_archive(document) == 1

    assert service.status()["entities"] == 0
    assert document["EVENT_EXTRACTION"]["entities"][0]["name"] == "白宫"
    assert observations.statistics()["resolution_statuses"] == {"not_found": 1}
    facts.close()
    observations.close()


def test_shadow_enrichment_builds_only_v2_unverified_candidates(tmp_path):
    facts = SQLiteAliasV2Store(str(tmp_path / "facts.db"))
    service = EntityAliasV2Service(facts)
    observations = EntityAliasV2ShadowStore(str(tmp_path / "observations.db"))
    extension = EntityAliasV2ShadowExtension(
        service,
        observations,
        v1_service=FakeV1(),
        suggester=FakeSuggester(),
    )
    extension.start(None)

    extension.observe_archive(_archive())
    assert extension.wait_for_idle(2.0)

    assert service.status()["entities"] == 1
    result = service.resolve("白宫", mention_type="facility")
    assert result.status == "unverified"
    entity = service.get_entity(result.candidates[0].entity_id)
    assert entity["refs"] == {"wikidata": "QWHITEHOUSE"}
    assert next(name for name in entity["names"]
                if name["name"] == "美国白宫")["resolution"] == "context_required"
    assert observations.statistics()["resolution_statuses"] == {"unverified": 1}
    assert extension.statistics["registered"] == 1
    extension.stop(None)
    facts.close()
    observations.close()


def test_same_qid_with_different_type_is_recorded_not_merged(tmp_path):
    facts = SQLiteAliasV2Store(str(tmp_path / "facts.db"))
    service = EntityAliasV2Service(facts)
    observations = EntityAliasV2ShadowStore(str(tmp_path / "observations.db"))
    extension = EntityAliasV2ShadowExtension(
        service, observations, suggester=FakeSuggester())

    assert extension._enrich("白宫", "facility")[0] == "registered"
    status, qid, detail = extension._enrich("白宫", "organization")

    assert status == "type_conflict"
    assert qid == "QWHITEHOUSE"
    assert "facility" in detail
    assert service.status()["entities"] == 1
    facts.close()
    observations.close()


def test_enrichment_ledger_prevents_restart_requery(tmp_path):
    observations = EntityAliasV2ShadowStore(str(tmp_path / "observations.db"))
    assert observations.should_enrich("白宫", "facility") is True
    observations.record_enrichment(
        "白宫", "facility", status="not_found", detail="none")
    observations.close()

    reopened = EntityAliasV2ShadowStore(str(tmp_path / "observations.db"))
    assert reopened.should_enrich("白宫", "facility") is False
    reopened.close()


def test_iis_factory_rejects_shared_fact_and_observation_database(tmp_path):
    class Config:
        values = {
            "entity_alias_v2_shadow.enabled": True,
            "entity_alias_v2_shadow.database": str(tmp_path / "same.db"),
            "entity_alias_v2_shadow.observations_database": str(tmp_path / "same.db"),
            "entity_alias_v2_shadow.wikidata.enabled": False,
        }

        def get(self, key, default=None):
            return self.values.get(key, default)

    try:
        create_entity_alias_v2_shadow(Config())
    except ValueError as exc:
        assert "必须使用不同文件" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("共享数据库应被拒绝")


def test_shadow_and_fact_schemas_do_not_overlap(tmp_path):
    facts_path = tmp_path / "facts.db"
    shadow_path = tmp_path / "shadow.db"
    facts = SQLiteAliasV2Store(str(facts_path))
    shadow = EntityAliasV2ShadowStore(str(shadow_path))

    with sqlite3.connect(facts_path) as connection:
        fact_tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }
    with sqlite3.connect(shadow_path) as connection:
        shadow_tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert "entities" in fact_tables and "observations" not in fact_tables
    assert "observations" in shadow_tables and "entities" not in shadow_tables
    facts.close()
    shadow.close()


def test_readonly_report_summarizes_separate_databases(tmp_path):
    root = tmp_path / "v2"
    facts = SQLiteAliasV2Store(str(root / "entities.db"))
    service = EntityAliasV2Service(facts)
    service.create_entity(
        "白宫建筑", canonical_type="facility", status="verified")
    shadow = EntityAliasV2ShadowStore(str(root / "shadow.db"))
    shadow.record(
        domain_name="news", source_uuid="article", local_entity_id="ENT1",
        surface="白宫", mention_type="organization", v1_hit=None,
        v2_result=service.resolve("白宫", mention_type="organization"),
    )

    report = build_report(root)

    assert report["facts"]["entities"] == 1
    assert report["shadow"]["observations"] == 1
    assert report["shadow"]["resolution_statuses"] == {"not_found": 1}
    facts.close()
    shadow.close()
