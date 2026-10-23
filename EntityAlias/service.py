# EntityAlias/service.py
# 实体别名服务门面：解析（精确→模糊→向量）、批量接口、CRUD。
#
# 协调 JSONLStore（事实源）+ AliasIndex（内存查询）+ 可选向量索引。

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from EntityAlias.index import AliasIndex
from EntityAlias.model import AliasEntry, AliasError, Entity
from EntityAlias.store import JSONLStore
from EntityAlias.vector import NullNearestIndex

logger = logging.getLogger(__name__)


class EntityAliasService:
    def __init__(self, store: JSONLStore, vector_index=None, suggester=None):
        self.store = store
        self.vector = vector_index if vector_index is not None else NullNearestIndex()
        self.suggester = suggester  # 可选在线建议源（WikidataSuggester）
        self._index = AliasIndex()
        self._lock = threading.RLock()
        self._index.rebuild(self.store.all())

    # ------------------------------------------------------------------ 解析

    def resolve(self, name: str, *, lang: str = "") -> Optional[Dict[str, Any]]:
        """传入一个名字，返回其标准名；未注册返回 None。

        lang 非空时附 display（该语言的标准名；'native' 表示母语标准名）。"""
        hit = self._index.resolve(name)
        if not hit:
            return None
        entity_id, matched_name, lang_tag, _ = hit
        entity = self.store.get(entity_id)
        if entity is None:
            return None
        result = {
            "entity_id": entity.id,
            "canonical": entity.canonical,
            "lang": entity.lang,
            "type": entity.type,
            "localized": dict(entity.localized),
            "native_lang": entity.native_lang,
            "description": entity.description,
            "status": entity.status,
            "matched_name": matched_name,
            "match": "exact",
        }
        if lang:
            result["display"] = entity.display_name(lang)
        return result

    def resolve_batch(self, names: List[str], *, lang: str = "") -> List[Dict[str, Any]]:
        """批量解析：每个输入名字返回 found/canonical 等。"""
        results = []
        for name in names:
            hit = self.resolve(name, lang=lang)
            results.append({
                "name": name,
                "found": hit is not None,
                **(hit or {}),
            })
        return results

    def expand_names(self, name: str) -> List[str]:
        """查询时展开：名字 → 其所属实体的全部已知名字（含自身）。未注册则原样返回。"""
        hit = self._index.resolve(name)
        if not hit:
            return [name]
        entity = self.store.get(hit[0])
        if entity is None:
            return [name]
        return [entry.name for entry in entity.all_names()]

    # ------------------------------------------------------------------ 在线建议

    def suggest(self, name: str, *, lang: str = "zh", limit: int = 5,
                online: bool = True) -> Dict[str, Any]:
        """本地优先：已注册直接命中；否则本地候选 + 可选在线 Wikidata 建议。"""
        local = self.lookup(name, limit=limit)
        result: Dict[str, Any] = {"name": name, "local": local, "wikidata": []}
        if local["found"] or not online or self.suggester is None:
            return result
        result["wikidata"] = self.suggester.suggest(name, lang=lang, limit=limit)
        return result

    def register_from_wikidata(self, qid: str, *, primary_lang: str = "zh",
                               overrides: Dict[str, Any] = None) -> Dict[str, Any]:
        """用 Wikidata 实体的多语言数据预填并注册到本地（AI 确认后调用）。"""
        if self.suggester is None:
            raise AliasError("在线建议源未配置", "bad_request")
        prefill = self.suggester.prefill(qid, primary_lang=primary_lang)
        if prefill is None:
            raise AliasError(f"Wikidata 实体不存在或拉取失败：{qid}", "not_found")
        for key, value in (overrides or {}).items():
            if value is not None:
                prefill[key] = value
        return self.create_entity(prefill.pop("canonical"), **prefill)

    # ------------------------------------------------------------------ 模糊查询

    def lookup(self, name: str, *, limit: int = 5, min_score: float = 0.4,
               use_vector: bool = True) -> Dict[str, Any]:
        """查找候选标准名：先精确，命中即返回；否则模糊/部分 + 可选向量召回。"""
        exact = self.resolve(name)
        candidates: List[Dict[str, Any]] = []
        if exact is None:
            candidates = self._index.fuzzy_lookup(name, limit=limit, min_score=min_score)
            for c in candidates:
                entity = self.store.get(c["entity_id"])
                if entity:
                    c["canonical"] = entity.canonical
                    c["type"] = entity.type
                    c["description"] = entity.description
                    c["status"] = entity.status
            if use_vector and self.vector.available:
                candidates = self._merge_vector(name, candidates, limit)
            candidates.sort(key=lambda c: c["score"], reverse=True)
            candidates = candidates[:limit]
        return {
            "name": name,
            "found": exact is not None,
            "exact": exact,
            "candidates": candidates,
        }

    def lookup_batch(self, names: List[str], *, limit: int = 5,
                     min_score: float = 0.4, use_vector: bool = True) -> List[Dict[str, Any]]:
        return [self.lookup(n, limit=limit, min_score=min_score, use_vector=use_vector)
                for n in names]

    def _merge_vector(self, name: str, candidates: List[Dict[str, Any]],
                      limit: int) -> List[Dict[str, Any]]:
        by_key = {(c["entity_id"], c["matched_name"]): c for c in candidates}
        for hit in self.vector.query(name, top_n=limit):
            entity_id = self._index.name_owner(hit["matched_name"])
            if not entity_id:
                continue
            key = (entity_id, hit["matched_name"])
            if key in by_key:
                by_key[key]["score"] = max(by_key[key]["score"], hit["score"])
                by_key[key]["source"] += "+vector"
                continue
            entity = self.store.get(entity_id)
            if entity is None:
                continue
            by_key[key] = {
                "entity_id": entity_id,
                "matched_name": hit["matched_name"],
                "lang": "",
                "is_canonical": hit["matched_name"] == entity.canonical,
                "canonical": entity.canonical,
                "type": entity.type,
                "description": entity.description,
                "status": entity.status,
                "score": hit["score"],
                "source": "vector",
            }
        return list(by_key.values())

    # ------------------------------------------------------------------ CRUD

    def create_entity(self, canonical: str, *, lang: str = "zh",
                      type: str = "other",
                      aliases: List[Dict[str, Any]] = None,
                      description: str = "",
                      status: str = "unverified",
                      localized: Dict[str, str] = None,
                      native_lang: str = "",
                      refs: Dict[str, str] = None) -> Dict[str, Any]:
        entity = Entity.new(
            canonical, lang=lang, type=type,
            aliases=[AliasEntry.from_dict(a) for a in (aliases or [])],
            description=description, status=status,
            localized=localized, native_lang=native_lang, refs=refs)
        entity.validate()
        with self._lock:
            self._check_name_conflicts(entity)
            self.store.put(entity)
            self._index.add_entity(entity)
        for entry in entity.all_names():
            self.vector.upsert_name(entry.name)
        return entity.to_dict()

    def update_entity(self, entity_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            stored = self.store.get(entity_id)
            if stored is None:
                raise AliasError(f"实体不存在：{entity_id}", "not_found")
            # 在副本上校验后再落盘，避免失败更新污染内存，也让 store 能识别类型迁移并清理旧文件。
            entity = Entity.from_dict(stored.to_dict())
            old_names = [e.name for e in entity.all_names()]
            if "canonical" in fields:
                entity.canonical = str(fields["canonical"]).strip()
            if "lang" in fields:
                entity.lang = str(fields["lang"]).strip().lower()
            if "type" in fields:
                entity.type = str(fields["type"]).strip().lower()
            if "aliases" in fields:
                entity.aliases = [AliasEntry.from_dict(a) for a in fields["aliases"]]
            if "description" in fields:
                entity.description = str(fields["description"] or "").strip()
            if "status" in fields:
                entity.status = str(fields["status"]).strip().lower()
            if "localized" in fields:
                entity.localized = {str(k).strip().lower(): str(v).strip()
                                    for k, v in (fields["localized"] or {}).items()}
            if "native_lang" in fields:
                entity.native_lang = str(fields["native_lang"] or "").strip().lower()
            if "refs" in fields:
                # refs 合并而非替换（允许多个外部来源共存）
                for k, v in (fields["refs"] or {}).items():
                    entity.refs[str(k).strip().lower()] = str(v).strip()
            entity.touch()
            entity.validate()
            self._check_name_conflicts(entity)
            self.store.put(entity)
            self._index.add_entity(entity)
        new_names = [e.name for e in entity.all_names()]
        for name in old_names:
            if name not in new_names:
                self.vector.remove_name(name)
        for name in new_names:
            self.vector.upsert_name(name)
        return entity.to_dict()

    def delete_entity(self, entity_id: str) -> bool:
        with self._lock:
            entity = self.store.get(entity_id)
            if entity is None:
                return False
            names = [e.name for e in entity.all_names()]
            self.store.delete(entity_id)
            self._index.remove_entity(entity_id)
        for name in names:
            self.vector.remove_name(name)
        return True

    def get_entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        entity = self.store.get(entity_id)
        return entity.to_dict() if entity else None

    def list_entities(self, *, type: Optional[str] = None, q: str = "",
                      limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        entities = self.store.all()
        if type:
            entities = [e for e in entities if e.type == type]
        if q:
            key = q.strip().lower()
            entities = [e for e in entities
                        if key in e.canonical.lower()
                        or any(key in a.name.lower() for a in e.aliases)]
        entities.sort(key=lambda e: e.canonical)
        total = len(entities)
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [e.to_dict() for e in entities[offset:offset + limit]],
        }

    def add_alias(self, entity_id: str, name: str, lang: str = "") -> Dict[str, Any]:
        entity = self.store.get(entity_id)
        if entity is None:
            raise AliasError(f"实体不存在：{entity_id}", "not_found")
        aliases = [a.to_dict() for a in entity.aliases] + [{"name": name, "lang": lang}]
        return self.update_entity(entity_id, {"aliases": aliases})

    def remove_alias(self, entity_id: str, name: str) -> Dict[str, Any]:
        entity = self.store.get(entity_id)
        if entity is None:
            raise AliasError(f"实体不存在：{entity_id}", "not_found")
        from EntityAlias.normalize import normalize_name
        key = normalize_name(name)
        aliases = [a.to_dict() for a in entity.aliases if normalize_name(a.name) != key]
        return self.update_entity(entity_id, {"aliases": aliases})

    def set_canonical(self, entity_id: str, name: str) -> Dict[str, Any]:
        """把一个已有别名/语言标准名提升为主标准名，并保留原主标准名。"""
        entity = self.store.get(entity_id)
        if entity is None:
            raise AliasError(f"实体不存在：{entity_id}", "not_found")
        from EntityAlias.normalize import normalize_name
        key = normalize_name(name)
        if not key:
            raise AliasError("标准名不能为空", "bad_request")
        if key == normalize_name(entity.canonical):
            return entity.to_dict()

        aliases = [a.to_dict() for a in entity.aliases]
        localized = dict(entity.localized)
        promoted_lang = ""
        found = False
        kept_aliases = []
        for alias in aliases:
            if not found and normalize_name(alias.get("name", "")) == key:
                promoted_lang = str(alias.get("lang") or "").strip().lower()
                found = True
                continue
            kept_aliases.append(alias)
        if not found:
            for lang, localized_name in list(localized.items()):
                if normalize_name(localized_name) == key:
                    promoted_lang = lang
                    localized.pop(lang, None)
                    found = True
                    break
        if not found:
            raise AliasError("只能把该实体已有的别名或语言标准名设为主标准名")

        old_canonical = entity.canonical
        old_lang = entity.lang
        new_lang = promoted_lang or old_lang
        same_language_name = localized.pop(new_lang, None) if new_lang else None
        if same_language_name and normalize_name(same_language_name) != key:
            kept_aliases.append({"name": same_language_name, "lang": new_lang})
        # 不丢失旧主标准名：语言不同且该语言槽空闲时保留为语言标准名，否则作为一般别名。
        if old_lang and old_lang != new_lang and old_lang not in localized:
            localized[old_lang] = old_canonical
        else:
            kept_aliases.append({"name": old_canonical, "lang": old_lang})
        return self.update_entity(entity_id, {
            "canonical": name,
            "lang": new_lang,
            "aliases": kept_aliases,
            "localized": localized,
        })

    # ------------------------------------------------------------------ 维护

    def rebuild_vector(self) -> int:
        """全量重建向量索引（upsert 幂等）。返回写入的名字数。"""
        names = [e.name for entity in self.store.all() for e in entity.all_names()]
        self.vector.rebuild(names)
        return len(names)

    def status(self) -> Dict[str, Any]:
        return {
            "entities": self.store.count(),
            "names_indexed": self._index.name_count,
            "vector_available": bool(self.vector.available),
            "online_suggest": self.suggester is not None,
            "data_files": list(self.store.files()),
        }

    # ------------------------------------------------------------------ 内部

    def _check_name_conflicts(self, entity: Entity) -> None:
        """同一归一化名只能属于一个实体。"""
        for entry in entity.all_names():
            owner = self._index.name_owner(entry.name)
            if owner and owner != entity.id:
                owner_entity = self.store.get(owner)
                owner_name = owner_entity.canonical if owner_entity else owner
                raise AliasError(
                    f"名称 {entry.name!r} 已被实体 {owner_name!r} 占用", "conflict")
