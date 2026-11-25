"""V2 正式路由：验证明显人物别名可合并，风险候选继续弃权。"""

import pytest

from EntityAliasV2 import (
    EntityAliasV2ProductionAdapter,
    EntityAliasV2Service,
    EntityRegistryRouter,
    SQLiteAliasV2Store,
)


@pytest.fixture
def adapter(tmp_path):
    store = SQLiteAliasV2Store(str(tmp_path / "v2.db"))
    service = EntityAliasV2Service(store)
    service.create_entity(
        "弗拉基米尔·普京",
        canonical_type="person",
        aliases=[{
            "name": "普京", "lang": "zh", "role": "abbr",
            "resolution": "context_required",
        }],
        status="unverified",
        refs={"wikidata": "Q7747"},
    )
    service.create_entity(
        "美国总统行政当局",
        canonical_type="organization",
        aliases=[{
            "name": "白宫", "role": "metonym",
            "resolution": "context_required",
        }],
        status="unverified",
        refs={"wikidata": "Q35525"},
    )
    yield EntityAliasV2ProductionAdapter(service)
    store.close()


def test_unique_wikidata_person_alias_resolves_with_country_context(adapter):
    hit = adapter.resolve_reference({
        "surface": "普京", "type": "person", "country_code": "RU",
    })

    assert hit["canonical"] == "弗拉基米尔·普京"
    assert hit["method"] == "v2_contextual_person"


def test_contextual_person_alias_abstains_without_country(adapter):
    assert adapter.resolve_reference({
        "surface": "普京", "type": "person",
    }) is None


def test_contextual_organization_never_uses_person_shortcut(adapter):
    assert adapter.resolve_reference({
        "surface": "白宫", "type": "organization", "country_code": "US",
    }) is None


def test_unique_wikidata_direct_name_can_use_external_trust(adapter):
    hit = adapter.resolve_reference({
        "surface": "弗拉基米尔·普京", "type": "person",
    })

    assert hit["method"] == "v2_trusted_external"


def test_same_type_ambiguity_still_abstains(tmp_path):
    store = SQLiteAliasV2Store(str(tmp_path / "ambiguous.db"))
    service = EntityAliasV2Service(store)
    for canonical, qid in (("甲地李明", "Q1"), ("乙地李明", "Q2")):
        service.create_entity(
            canonical, canonical_type="person",
            aliases=[{"name": "李明", "resolution": "context_required"}],
            refs={"wikidata": qid},
        )
    adapter = EntityAliasV2ProductionAdapter(service)

    assert adapter.resolve_reference({
        "surface": "李明", "type": "person", "country_code": "CN",
    }) is None
    store.close()


def test_router_modes_are_reversible(adapter):
    class Legacy:
        def resolve(self, name):
            return {"entity_id": "ent_v1", "canonical": name, "type": "person"}

        def get_entity(self, entity_id):
            return None

    request = {"surface": "普京", "type": "person", "country_code": "RU"}

    assert EntityRegistryRouter(
        mode="shadow", v2=adapter, legacy=Legacy(),
    ).resolve_reference(request)["entity_id"] == "ent_v1"
    assert EntityRegistryRouter(
        mode="prefer_v2", v2=adapter, legacy=Legacy(),
    ).resolve_reference(request)["entity_id"].startswith("ent2_")
    assert EntityRegistryRouter(
        mode="v2_only", v2=adapter, legacy=Legacy(),
    ).resolve_reference({"surface": "未知", "type": "person"}) is None
