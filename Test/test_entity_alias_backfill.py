# -*- coding: utf-8 -*-
"""实体别名历史补录脚本的 Wikidata 自动注册测试。"""

from EntityAlias import EntityAliasService, JSONLStore
from EntityAlias.wikidata import WikidataSuggester
from Scripts.entity_alias_backfill import (
    _register_wikidata_item,
    _select_exact_wikidata,
    count_document_entities,
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


def test_count_document_entities_prefers_extraction_without_double_counting():
    from collections import Counter
    counters = {}

    # 新结构：遍历 EVENT_EXTRACTION.entities 全部类型，速查字段不重复计数
    doc = {
        "EVENT_EXTRACTION": {"entities": [
            {"name": "甲军", "type": "organization"},
            {"name": "某设施", "type": "facility"},
        ]},
        "PEOPLE": ["张三"],          # 速查字段应与抽取结果重复，不得再计
        "ORGANIZATION": ["甲军"],
    }
    assert count_document_entities(doc, counters) == "extraction"
    assert counters["organization"]["甲军"] == 1
    assert counters["facility"]["某设施"] == 1
    assert "person" not in counters

    # 旧文档缺少抽取结构 → 回退三个速查字段
    legacy = {"PEOPLE": ["张三"], "LOCATION": ["某地"], "ORGANIZATION": ["乙公司"]}
    assert count_document_entities(legacy, counters) == "fallback"
    assert counters["person"]["张三"] == 1
    assert counters["location"]["某地"] == 1
    assert counters["organization"]["乙公司"] == 1

    # EVENT_EXTRACTION 为 None（NonIntelligence）→ 也走回退
    assert count_document_entities({"EVENT_EXTRACTION": None}, counters) == "none"
