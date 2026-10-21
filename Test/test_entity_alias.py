# -*- coding: utf-8 -*-
"""EntityAlias 子系统单元测试：归一化、存储、解析、模糊/向量召回、CRUD、API。"""

import json

import pytest
from flask import Flask

from EntityAlias import EntityAliasService, JSONLStore
from EntityAlias.iis_integration import create_entity_alias, mount_to_iis
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


def test_set_canonical_promotes_existing_name_and_preserves_old_name(tmp_path):
    svc = _make_service(tmp_path)
    entity = svc.create_entity(
        "纽约时报", lang="zh", type="organization",
        localized={"en": "The New York Times"},
        aliases=[{"name": "NYT", "lang": "en", "role": "abbr"}])

    promoted = svc.set_canonical(entity["id"], "The New York Times")

    assert promoted["canonical"] == "The New York Times"
    assert promoted["lang"] == "en"
    assert promoted["localized"]["zh"] == "纽约时报"
    assert "en" not in promoted["localized"]
    assert svc.resolve("纽约时报")["canonical"] == "The New York Times"
    assert svc.resolve("NYT")["canonical"] == "The New York Times"


def test_update_type_rewrites_old_type_file(tmp_path):
    svc = _make_service(tmp_path)
    entity = svc.create_entity("测试机构", type="organization")

    svc.update_entity(entity["id"], {"type": "other"})

    assert "测试机构" not in (tmp_path / "organization.jsonl").read_text(encoding="utf-8")
    assert "测试机构" in (tmp_path / "other.jsonl").read_text(encoding="utf-8")


# ---------------------------------------------------------------- meta 字段与展开

def test_meta_fields_and_roles(tmp_path):
    svc = _make_service(tmp_path)
    entity = svc.create_entity(
        "纽约时报", lang="zh", type="organization", status="verified",
        description="美国主流媒体",
        aliases=[{"name": "The New York Times", "lang": "en", "role": "native"},
                 {"name": "NYT", "lang": "en", "role": "abbr"}])
    assert entity["status"] == "verified" and entity["description"] == "美国主流媒体"
    assert entity["aliases"][1]["role"] == "abbr"

    # lookup 候选携带 description/status（供 AI 判断是否同一实体）
    result = svc.lookup("NYTimes")
    assert result["candidates"][0]["description"] == "美国主流媒体"

    # 非法 role / status 被拒绝
    with pytest.raises(AliasError):
        svc.create_entity("X社", aliases=[{"name": "X", "role": "bogus"}])
    with pytest.raises(AliasError):
        svc.create_entity("Y社", status="bogus")

    # 重启后 meta 保持
    svc2 = _make_service(tmp_path)
    assert svc2.resolve("NYT")["status"] == "verified"


def test_expand_names(tmp_path):
    svc = _make_service(tmp_path)
    svc.create_entity("纽约时报", aliases=[{"name": "NYT", "lang": "en"},
                                           {"name": "The New York Times", "lang": "en"}])
    assert sorted(svc.expand_names("NYT")) == sorted(
        ["纽约时报", "NYT", "The New York Times"])
    assert svc.expand_names("未注册名字") == ["未注册名字"]


# ---------------------------------------------------------------- 多语言标准名

def test_localized_canonical(tmp_path):
    svc = _make_service(tmp_path)
    entity = svc.create_entity(
        "纽约时报", lang="zh", type="organization", native_lang="en",
        localized={"en": "The New York Times"},
        aliases=[{"name": "NYT", "lang": "en", "role": "abbr"}])

    # 英文标准名不是别名：可解析，且按语言返回对应标准名
    hit = svc.resolve("The New York Times")
    assert hit and hit["canonical"] == "纽约时报"
    assert svc.resolve("NYT", lang="en")["display"] == "The New York Times"
    # 母语模式：native_lang=en → 母语标准名
    assert svc.resolve("NYT", lang="native")["display"] == "The New York Times"
    # 无对应语言版本时回落主标准名
    assert svc.resolve("NYT", lang="fr")["display"] == "纽约时报"
    # 默认不指定语言 → 主标准名
    assert svc.resolve("NYT")["canonical"] == "纽约时报"

    # 语言标准名进唯一性索引：其它实体占用即 409
    with pytest.raises(AliasError) as exc_info:
        svc.create_entity("另一个报社", aliases=[{"name": "The New York Times"}])
    assert exc_info.value.code == "conflict"

    # 查询时展开覆盖语言标准名
    assert sorted(svc.expand_names("NYT")) == sorted(
        ["纽约时报", "The New York Times", "NYT"])

    # 重启后 localized 保持
    svc2 = _make_service(tmp_path)
    assert svc2.resolve("NYT", lang="en")["display"] == "The New York Times"

    # localized 与主标准名重复被拒绝
    with pytest.raises(AliasError):
        svc.create_entity("新华社", localized={"zh": "新华社"})


# ---------------------------------------------------------------- 查询时展开（方案 A）

def test_query_engine_alias_expansion():
    from ServiceComponent.EventV4QueryEngine import EventV4QueryEngine

    class FakeCursor:
        def sort(self, *a): return self
        def skip(self, *a): return self
        def limit(self, *a): return self
        def __iter__(self): return iter([])

    class FakeCollection:
        def __init__(self): self.last_query = None
        def count_documents(self, q): self.last_query = q; return 0
        def find(self, q, projection=None): self.last_query = q; return FakeCursor()

    collection = FakeCollection()
    engine = EventV4QueryEngine(collection, None)

    # 未注入 expander：原样 $in
    engine.query_intelligence(organizations=["NYT"])
    assert collection.last_query["$and"][0]["ORGANIZATION"]["$in"] == ["NYT"]

    # 注入后：展开为实体的全部已知名字
    engine.alias_expander = lambda name: {"NYT": ["NYT", "纽约时报"]}.get(name, [name])
    engine.query_intelligence(organizations=["NYT"])
    assert collection.last_query["$and"][0]["ORGANIZATION"]["$in"] == ["NYT", "纽约时报"]


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
        "localized": {"en": "People's Daily"},
        "aliases": [{"name": "人民日报社", "lang": "zh", "role": "full"}]})

    r = api_client.get("/entity-alias/api/resolve?name=People's Daily")
    assert r.status_code == 200 and r.get_json()["canonical"] == "人民日报"

    # 语言切换：返回该语言标准名
    r = api_client.get("/entity-alias/api/resolve?name=人民日报社&lang=en")
    assert r.get_json()["display"] == "People's Daily"

    r = api_client.post("/entity-alias/api/resolve_batch",
                        json={"names": ["人民日报", "未知"], "lang": "en"})
    results = r.get_json()["results"]
    assert results[0]["found"] and results[0]["display"] == "People's Daily"
    assert not results[1]["found"]

    r = api_client.get("/entity-alias/api/lookup?name=人民日")
    assert r.get_json()["candidates"][0]["canonical"] == "人民日报"

    r = api_client.get("/entity-alias/api/status")
    assert r.get_json()["entities"] == 1


def test_api_conflict_returns_409(api_client):
    api_client.post("/entity-alias/api/entities", json={"canonical": "人民日报"})
    r = api_client.post("/entity-alias/api/entities", json={"canonical": "人民日报"})
    assert r.status_code == 409
    assert r.get_json()["code"] == "conflict"


def test_management_page_and_set_canonical_api(api_client):
    created = api_client.post("/entity-alias/api/entities", json={
        "canonical": "美利坚合众国", "type": "country",
        "aliases": [{"name": "美国", "lang": "zh", "role": "abbr"}],
    }).get_json()

    page = api_client.get("/entity-alias/manage")
    assert page.status_code == 200
    assert "全局实体别名管理" in page.get_data(as_text=True)
    assert "本页不管理语境实体" in page.get_data(as_text=True)

    response = api_client.post(
        f"/entity-alias/api/entities/{created['id']}/canonical",
        json={"name": "美国"},
    )
    assert response.status_code == 200
    updated = response.get_json()
    assert updated["canonical"] == "美国"
    assert any(alias["name"] == "美利坚合众国" for alias in updated["aliases"])


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


def test_create_then_mount_reuses_same_service(tmp_path):
    class FakeConfig:
        def get(self, key, default=None):
            return {"entity_alias.data_dir": str(tmp_path)}.get(key, default)

    config = FakeConfig()
    service = create_entity_alias(config)
    service.create_entity("人民日报", aliases=[{"name": "People's Daily"}])
    app = Flask(__name__)

    mounted = mount_to_iis(
        app, config=config, wrapper=None, service=service)

    assert mounted is service
    response = app.test_client().get(
        "/entity-alias/api/resolve?name=People's%20Daily")
    assert response.get_json()["canonical"] == "人民日报"


def test_iis_config_projection():
    """optional_subsystems.entity_alias 经 IISConfig 投影为 entity_alias.* 运行时键。"""
    import os
    from IISConfig import load_iis_config

    example = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "_config", "iis_example.yaml")
    config = load_iis_config(example)
    assert config.get("entity_alias.enabled") is True
    assert config.get("entity_alias.url_prefix") == "/entity-alias"
    assert config.get("entity_alias.wikidata.min_interval_sec") == 0.5
    assert config.get("entity_alias.wikidata.langs") == "zh,en"
