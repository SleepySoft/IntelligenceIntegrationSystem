# -*- coding: utf-8 -*-
"""EntityAlias Wikidata 建议源测试（fake transport，不发真实网络请求）。"""

import time

import pytest

from EntityAlias import EntityAliasService, JSONLStore
from EntityAlias.model import AliasError
from EntityAlias.wikidata import WikidataSuggester, _RateLimited, proxy_uses_socks


NYT_ENTITY = {
    "id": "Q9684",
    "labels": {"en": {"value": "The New York Times"},
               "zh": {"value": "纽约时报"}},
    "descriptions": {"en": {"value": "American daily newspaper"},
                     "zh": {"value": "美国报纸"}},
    "aliases": {"en": [{"value": "NYT"}, {"value": "The Times"}]},
}


def make_transport(calls_log=None, fail_times=0):
    state = {"failed": 0}

    def transport(url, params):
        if calls_log is not None:
            calls_log.append(params)
        if state["failed"] < fail_times:
            state["failed"] += 1
            raise _RateLimited(retry_after=0.01)
        if params.get("action") == "wbsearchentities":
            return {"search": [{
                "id": "Q9684", "label": "The New York Times",
                "description": "American daily newspaper",
                "match": {"text": "NYT"},
            }]}
        if params.get("action") == "wbgetentities":
            return {"entities": {"Q9684": NYT_ENTITY}}
        return {}

    return transport


def test_suggest_and_prefill():
    suggester = WikidataSuggester(transport=make_transport(), min_interval=0)
    hits = suggester.suggest("NYT", lang="en")
    assert hits and hits[0]["qid"] == "Q9684"

    prefill = suggester.prefill("Q9684", primary_lang="zh")
    assert prefill["canonical"] == "纽约时报"
    assert prefill["lang"] == "zh"
    assert prefill["localized"] == {"en": "The New York Times"}
    assert prefill["refs"] == {"wikidata": "Q9684"}
    assert prefill["description"] == "美国报纸"
    assert {a["name"] for a in prefill["aliases"]} >= {"NYT", "The Times"}


def test_prefill_fallback_to_english():
    # 无中文 label 时回落英文
    entity = {"Q1": {"id": "Q1",
                     "labels": {"en": {"value": "Universe"}},
                     "descriptions": {}, "aliases": {}}}
    suggester = WikidataSuggester(
        transport=lambda url, params: {"entities": entity}, min_interval=0)
    prefill = suggester.prefill("Q1", primary_lang="zh")
    assert prefill["canonical"] == "Universe" and prefill["lang"] == "en"


def test_prefill_deduplicates_aliases_against_labels_and_other_aliases():
    entity = {"Q1": {
        "id": "Q1",
        "labels": {"zh": {"value": "唐纳德·特朗普"},
                   "en": {"value": "Donald Trump"}},
        "descriptions": {},
        "aliases": {
            "zh": [{"value": "唐纳德·特朗普"}, {"value": "川普"}],
            "en": [{"value": "Donald Trump"}, {"value": "Trump"},
                   {"value": "Trump"}],
        },
    }}
    suggester = WikidataSuggester(
        transport=lambda url, params: {"entities": entity}, min_interval=0)
    prefill = suggester.prefill("Q1")
    assert prefill["aliases"] == [
        {"name": "川普", "lang": "zh"},
        {"name": "Trump", "lang": "en"},
    ]


def test_rate_limit_backoff():
    calls = []
    suggester = WikidataSuggester(transport=make_transport(calls, fail_times=2),
                                  min_interval=0)
    hits = suggester.suggest("NYT")
    assert hits and len(calls) == 3  # 两次 429 后成功


def test_cache(tmp_path):
    calls = []
    suggester = WikidataSuggester(
        transport=make_transport(calls), min_interval=0,
        cache_path=str(tmp_path / "c.sqlite"))
    suggester.suggest("NYT")
    suggester.suggest("NYT")
    assert len(calls) == 1  # 第二次命中缓存


def test_service_local_first_and_register_from_wikidata(tmp_path):
    svc = EntityAliasService(JSONLStore(str(tmp_path)),
                             suggester=WikidataSuggester(
                                 transport=make_transport(), min_interval=0))

    # 本地已注册 → 不发在线请求
    svc.create_entity("人民日报")
    result = svc.suggest("人民日报")
    assert result["local"]["found"] is True and result["wikidata"] == []

    # 本地未注册 → 返回在线建议
    result = svc.suggest("NYT")
    assert result["local"]["found"] is False
    assert result["wikidata"][0]["qid"] == "Q9684"

    # AI 确认后补录回本地
    entity = svc.register_from_wikidata("Q9684")
    assert entity["canonical"] == "纽约时报"
    assert entity["refs"]["wikidata"] == "Q9684"
    # 补录后本地可解析
    assert svc.resolve("NYT")["canonical"] == "纽约时报"
    assert svc.suggest("NYT")["local"]["found"] is True

    # 在线源未配置时注册报错
    svc2 = EntityAliasService(JSONLStore(str(tmp_path / "d2")))
    with pytest.raises(AliasError):
        svc2.register_from_wikidata("Q9684")


def test_refs_merge_on_update(tmp_path):
    svc = EntityAliasService(JSONLStore(str(tmp_path)))
    entity = svc.create_entity("新华社", refs={"wikidata": "Q204839"})
    svc.update_entity(entity["id"], {"refs": {"geonames": "123"}})
    assert svc.get_entity(entity["id"])["refs"] == {
        "wikidata": "Q204839", "geonames": "123"}


def test_proxy_mapping_is_supported():
    proxy = {"http": "socks5://127.0.0.1:10808",
             "https": "socks5://127.0.0.1:10808"}
    assert proxy_uses_socks(proxy) is True

    captured = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"search": []}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return Response()

    from unittest.mock import patch
    suggester = WikidataSuggester(proxy=proxy, min_interval=0)
    with patch("requests.get", fake_get):
        suggester.suggest("NYT")
    assert captured["proxies"] == proxy
