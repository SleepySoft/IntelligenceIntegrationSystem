# -*- coding: utf-8 -*-
"""EntityAlias 子系统单元测试：归一化、存储、解析、模糊/向量召回、CRUD、API。"""

import json

import pytest
from flask import Flask

from EntityAlias import EntityAliasService, JSONLStore
from EntityAlias.iis_integration import mount_to_iis
from EntityAlias.model import AliasEntry, AliasError, Entity
from EntityAlias.normalize import display_normalize, normalize_name
from EntityAlias.web import EntityAliasWebService


# ---------------------------------------------------------------- 归一化

def test_normalize_name():
    assert normalize_name(" People's Daily ") == "peoplesdaily"
    assert normalize_name("人民日报") == "人民日报"
    assert normalize_name("U.S.A.") == "usa"
    assert normalize_name("") == ""


# ---------------------------------------------------------------- 存储

def _make_service(tmp_path, vector=None):
    return EntityAliasService(JSONLStore(str(tmp_path)), vector)


def test_store_roundtrip(tmp_path):
    svc = _make_service(tmp_path)
    svc.create_entity("人民日报", lang="zh", type="organization",
                      aliases=[{"name": "People's Daily", "lang": "en"}])

    # 重新加载（模拟重启）
    svc2 = _make_service(tmp_path)
    hit = svc2.resolve("people's daily")
    assert hit and hit["canonical"] == "人民日报"

    # JSONL 文件确实写入且可人工阅读
    text = (tmp_path / "organization.jsonl").read_text(encoding="utf-8")
    assert "人民日报" in text and "People's Daily" in text


# ---------------------------------------------------------------- 解析

def test_resolve_exact_across_languages(tmp_path):
    svc = _make_service(tmp_path)
    svc.create_entity("人民日报", lang="zh", type="organization",
                      aliases=[{"name": "People's Daily", "lang": "en"}])
    assert svc.resolve("人民日报")["canonical"] == "人民日报"
    assert svc.resolve("PEOPLES DAILY")["canonical"] == "人民日报"
    assert svc.resolve("不存在的名字") is None


def test_resolve_batch(tmp_path):
    svc = _make_service(tmp_path)
    svc.create_entity("人民日报", type="organization")
    results = svc.resolve_batch(["人民日报", "未知实体"])
    assert results[0]["found"] is True
    assert results[1]["found"] is False


# ---------------------------------------------------------------- 模糊/部分

def test_lookup_partial_and_fuzzy(tmp_path):
    svc = _make_service(tmp_path)
    svc.create_entity("人民日报", type="organization")
    svc.create_entity("中国人民公安大学", type="organization")

    # 部分匹配（包含关系）
    result = svc.lookup("人民日")
    assert result["found"] is False
    assert result["candidates"][0]["canonical"] == "人民日报"
    assert result["candidates"][0]["source"] == "partial"

    # 精确命中时无候选
    result = svc.lookup("人民日报")
    assert result["found"] is True and result["exact"]["canonical"] == "人民日报"

    # 模糊匹配（错字）
    result = svc.lookup("人民日扳")
    assert any(c["canonical"] == "人民日报" for c in result["candidates"])


# ---------------------------------------------------------------- 向量召回

class FakeVectorIndex:
    available = True

    def __init__(self):
        self.names = []

    def upsert_name(self, name):
        self.names.append(name)

    def remove_name(self, name):
        pass

    def rebuild(self, names):
        self.names = list(names)

    def query(self, text, top_n=5):
        if "贸易" in text:
            return [{"matched_name": "关税同盟", "score": 0.88, "source": "vector"}]
        return []


def test_lookup_vector_fallback(tmp_path):
    svc = _make_service(tmp_path, vector=FakeVectorIndex())
    svc.create_entity("关税同盟", type="organization")
    result = svc.lookup("贸易协定组织")
    assert result["found"] is False
    vector_hits = [c for c in result["candidates"] if "vector" in c["source"]]
    assert vector_hits and vector_hits[0]["canonical"] == "关税同盟"


# ---------------------------------------------------------------- CRUD

def test_crud_and_conflict(tmp_path):
    svc = _make_service(tmp_path)
    entity = svc.create_entity("人民日报", aliases=[{"name": "人民网", "lang": "zh"}])

    # 名字冲突：别名已被占用
    with pytest.raises(AliasError) as exc_info:
        svc.create_entity("另一个实体", aliases=[{"name": "人民网", "lang": "zh"}])
    assert exc_info.value.code == "conflict"

    # 标准名冲突（归一化不敏感：首尾空白忽略）
    with pytest.raises(AliasError):
        svc.create_entity(" 人民日报 ")

    # 更新 + 别名增删
    svc.add_alias(entity["id"], "人民日报海外版", "zh")
    assert svc.resolve("人民日报海外版")["entity_id"] == entity["id"]
    svc.remove_alias(entity["id"], "人民日报海外版")
    assert svc.resolve("人民日报海外版") is None

    # 删除
    assert svc.delete_entity(entity["id"]) is True
    assert svc.resolve("人民网") is None


# ---------------------------------------------------------------- API

@pytest.fixture
def api_client(tmp_path):
    app = Flask(__name__)
    svc = _make_service(tmp_path / "data")
    EntityAliasWebService(svc).mount_to_app(app, url_prefix="/entity-alias")
    return app.test_client()


def test_api_resolve_and_lookup(api_client):
    api_client.post("/entity-alias/api/entities", json={
        "canonical": "人民日报", "type": "organization",
        "aliases": [{"name": "People's Daily", "lang": "en"}]})

    r = api_client.get("/entity-alias/api/resolve?name=People's Daily")
    assert r.status_code == 200 and r.get_json()["canonical"] == "人民日报"

    r = api_client.post("/entity-alias/api/resolve_batch",
                        json={"names": ["人民日报", "未知"]})
    results = r.get_json()["results"]
    assert results[0]["found"] and not results[1]["found"]

    r = api_client.get("/entity-alias/api/lookup?name=人民日")
    assert r.get_json()["candidates"][0]["canonical"] == "人民日报"

    r = api_client.get("/entity-alias/api/status")
    assert r.get_json()["entities"] == 1


def test_api_conflict_returns_409(api_client):
    api_client.post("/entity-alias/api/entities", json={"canonical": "人民日报"})
    r = api_client.post("/entity-alias/api/entities", json={"canonical": "人民日报"})
    assert r.status_code == 409
    assert r.get_json()["code"] == "conflict"


def test_mount_to_iis(tmp_path):
    class FakeConfig:
        def get(self, key, default=None):
            return {"entity_alias.data_dir": str(tmp_path)}.get(key, default)

    app = Flask(__name__)
    svc = mount_to_iis(app, config=FakeConfig(), wrapper=None, vector_client=None)
    assert svc is not None
    client = app.test_client()
    assert client.get("/entity-alias/api/status").status_code == 200
    # 向量未配置时 status 反映不可用
    assert client.get("/entity-alias/api/status").get_json()["vector_available"] is False
