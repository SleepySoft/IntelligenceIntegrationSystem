"""EntityAlias V2：独立 SQLite 存储与保守类型化解析。"""

import sqlite3

import pytest

from EntityAliasV2 import (
    AliasV2Error,
    EntityAliasV2Service,
    SQLiteAliasV2Store,
    normalize_entity_type,
)


@pytest.fixture
def service(tmp_path):
    store = SQLiteAliasV2Store(str(tmp_path / "entity_alias_v2.db"))
    value = EntityAliasV2Service(store)
    yield value
    store.close()


def test_sqlite_is_independent_fact_source_and_survives_restart(tmp_path):
    path = tmp_path / "entity_alias_v2.db"
    first = SQLiteAliasV2Store(str(path))
    service = EntityAliasV2Service(first)
    created = service.create_entity(
        "苹果公司",
        canonical_type="organization",
        canonical_lang="zh",
        localized={"en": "Apple Inc."},
        aliases=[{"name": "苹果", "lang": "zh", "role": "abbr"}],
        status="verified",
        refs={"wikidata": "Q312"},
    )
    first.close()

    second = SQLiteAliasV2Store(str(path))
    restored = EntityAliasV2Service(second)
    assert restored.get_entity(created["id"])["canonical"] == "苹果公司"
    assert restored.resolve(
        "Apple Inc.", mention_type="organization").resolved.entity_id == created["id"]
    assert restored.status()["v1_imported"] is False
    second.close()


def test_entity_type_lives_outside_aliases(service):
    entity = service.create_entity(
        "白宫",
        canonical_type="facility",
        aliases=[{"name": "The White House", "lang": "en"}],
        status="verified",
    )

    assert entity["canonical_type"] == "facility"
    assert all("canonical_type" not in name for name in entity["names"])
    assert service.resolve(
        "The White House", mention_type="facility").status == "resolved"


def test_same_name_different_types_resolve_to_different_entities(service):
    building = service.create_entity(
        "白宫建筑", canonical_type="facility",
        aliases=[{"name": "白宫"}], status="verified")
    administration = service.create_entity(
        "美国总统行政当局", canonical_type="organization",
        aliases=[{"name": "白宫", "role": "metonym"}], status="verified")

    assert service.resolve(
        "白宫", mention_type="facility").resolved.entity_id == building["id"]
    assert service.resolve(
        "白宫", mention_type="organization").resolved.entity_id == administration["id"]


def test_same_name_same_type_returns_ambiguous(service):
    service.create_entity(
        "甲地中央公园", canonical_type="location",
        aliases=[{"name": "中央公园"}], status="verified")
    service.create_entity(
        "乙地中央公园", canonical_type="location",
        aliases=[{"name": "中央公园"}], status="verified")

    result = service.resolve("中央公园", mention_type="location")

    assert result.status == "ambiguous"
    assert len(result.candidates) == 2
    assert result.resolved is None


def test_missing_type_never_auto_resolves(service):
    service.create_entity(
        "人民日报", canonical_type="organization", status="verified")

    result = service.resolve("人民日报", mention_type=None)

    assert result.status == "ambiguous"
    assert len(result.candidates) == 1
    assert result.resolved is None


def test_type_conflict_reports_other_candidates_without_merging(service):
    service.create_entity(
        "白宫建筑", canonical_type="facility",
        aliases=[{"name": "白宫"}], status="verified")

    result = service.resolve("白宫", mention_type="organization")

    assert result.status == "type_conflict"
    assert not result.candidates
    assert result.other_type_candidates[0].canonical_type == "facility"


def test_context_required_alias_abstains_even_when_unique(service):
    service.create_entity(
        "美国总统行政当局",
        canonical_type="organization",
        aliases=[{
            "name": "白宫",
            "role": "metonym",
            "resolution": "context_required",
        }],
        status="verified",
    )

    result = service.resolve("白宫", mention_type="organization")

    assert result.status == "context_required"
    assert result.resolved is None


def test_unverified_candidate_abstains_by_default(service):
    entity = service.create_entity(
        "测试机构", canonical_type="organization", status="unverified")

    assert service.resolve(
        "测试机构", mention_type="organization").status == "unverified"
    assert service.resolve(
        "测试机构", mention_type="organization",
        allow_unverified=True).resolved.entity_id == entity["id"]


def test_legacy_type_is_normalized_before_lookup(service):
    entity = service.create_entity(
        "美国", canonical_type="geopolitical_entity", status="verified")

    result = service.resolve("美国", mention_type="country")

    assert normalize_entity_type("country") == "geopolitical_entity"
    assert result.canonical_type == "geopolitical_entity"
    assert result.resolved.entity_id == entity["id"]


def test_unknown_type_is_not_broadened_to_other_object(service):
    service.create_entity(
        "未知对象", canonical_type="other_object", status="verified")

    result = service.resolve("未知对象", mention_type="made_up_type")

    assert result.status == "invalid_type"
    assert result.resolved is None


def test_same_normalized_name_is_only_unique_inside_one_entity(service):
    service.create_entity(
        "甲公司", canonical_type="organization",
        aliases=[{"name": "ACME"}], status="verified")
    # V2 允许跨实体重名，甚至允许同名同类型；解析时必须返回 ambiguous。
    service.create_entity(
        "乙公司", canonical_type="organization",
        aliases=[{"name": "A.C.M.E."}], status="verified")
    assert service.resolve("ACME", mention_type="organization").status == "ambiguous"

    with pytest.raises(AliasV2Error) as exc_info:
        service.create_entity(
            "丙公司", canonical_type="organization",
            aliases=[{"name": "丙公司"}])
    assert exc_info.value.code == "conflict"


def test_external_reference_is_globally_unique(service):
    service.create_entity(
        "苹果公司", canonical_type="organization", refs={"wikidata": "Q312"})

    with pytest.raises(AliasV2Error) as exc_info:
        service.create_entity(
            "另一个苹果", canonical_type="organization",
            refs={"wikidata": "Q312"})
    assert exc_info.value.code == "conflict"


def test_type_update_moves_derived_typed_key(service):
    entity = service.create_entity(
        "示例对象", canonical_type="facility", status="verified")

    service.update_entity(entity["id"], canonical_type="organization")

    assert service.resolve(
        "示例对象", mention_type="facility").status == "type_conflict"
    assert service.resolve(
        "示例对象", mention_type="organization").status == "resolved"


def test_promote_existing_alias_to_canonical(service):
    entity = service.create_entity(
        "纽约时报", canonical_type="organization", canonical_lang="zh",
        localized={"en": "The New York Times"},
        aliases=[{"name": "NYT", "lang": "en", "role": "abbr"}],
        status="verified")

    updated = service.set_canonical(entity["id"], "The New York Times")

    assert updated["canonical"] == "The New York Times"
    assert service.resolve("纽约时报", mention_type="organization").status == "resolved"


def test_failed_update_rolls_back_transaction(service):
    entity = service.create_entity(
        "回滚测试", canonical_type="organization", status="verified",
        refs={"wikidata": "Q1"})
    service.create_entity(
        "另一个实体", canonical_type="organization", refs={"wikidata": "Q2"})

    with pytest.raises(AliasV2Error):
        service.update_entity(entity["id"], refs={"wikidata": "Q2"})

    assert service.get_entity(entity["id"])["refs"] == {"wikidata": "Q1"}


def test_database_schema_does_not_duplicate_type_on_names(service):
    service.create_entity("结构测试", canonical_type="topic")
    connection = sqlite3.connect(service.store.path)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(entity_names)")
    }
    connection.close()

    assert "canonical_type" not in columns
    assert "mention_type" not in columns
