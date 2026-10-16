# -*- coding: utf-8 -*-
"""实体别名历史补录脚本的 Wikidata 自动注册测试。"""

from EntityAlias import EntityAliasService, JSONLStore
from EntityAlias.wikidata import WikidataSuggester
from Scripts.entity_alias_backfill import (
    _register_wikidata_item,
    _select_exact_wikidata,
)


def _transport(url, params):
    assert params["action"] == "wbgetentities"
    return {"entities": {"Q159": {
        "id": "Q159",
        "labels": {
            "zh": {"value": "俄罗斯"},
            "en": {"value": "Russia"},
        },
        "descriptions": {"zh": {"value": "欧洲和亚洲的国家"}},
        "aliases": {"en": [{"value": "Russian Federation"}]},
    }}}


def test_select_exact_wikidata_requires_unique_exact_name():
    hits = [{"qid": "Q159", "label": "俄罗斯", "matched": "俄罗斯"},
            {"qid": "Q2", "label": "俄罗斯其他", "matched": ""}]
    assert _select_exact_wikidata("俄 罗斯", hits)["qid"] == "Q159"
    assert _select_exact_wikidata("俄罗斯", hits + [
        {"qid": "Q3", "label": "俄罗斯", "matched": ""}
    ]) is None


def test_register_wikidata_item_is_idempotent_and_adds_input_alias(tmp_path):
    service = EntityAliasService(
        JSONLStore(str(tmp_path)),
        suggester=WikidataSuggester(transport=_transport, min_interval=0),
    )
    item = {"name": "俄罗斯联邦", "field": "LOCATION"}
    candidate = {"qid": "Q159", "description": "country in Europe and Asia"}

    first = _register_wikidata_item(service, item, "Q159", candidate)
    second = _register_wikidata_item(service, item, "Q159", candidate)

    assert first["id"] == second["id"]
    assert second["type"] == "location"
    assert second["localized"] == {"en": "Russia"}
    assert second["refs"] == {"wikidata": "Q159"}
    assert service.resolve("俄罗斯联邦")["entity_id"] == first["id"]
    assert service.status()["entities"] == 1
