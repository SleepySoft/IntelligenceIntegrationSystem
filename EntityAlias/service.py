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
    def __init__(self, store: JSONLStore, vector_index=None):
        self.store = store
        self.vector = vector_index if vector_index is not None else NullNearestIndex()
        self._index = AliasIndex()
        self._lock = threading.RLock()
        self._index.rebuild(self.store.all())

    # ------------------------------------------------------------------ 解析

    def resolve(self, name: str) -> Optional[Dict[str, Any]]:
        """传入一个名字，返回其标准名；未注册返回 None。"""
        hit = self._index.resolve(name)
        if not hit:
            return None
        entity_id, matched_name, lang, _ = hit
        entity = self.store.get(entity_id)
        if entity is None:
            return None
        return {
            "entity_id": entity.id,
            "canonical": entity.canonical,
            "lang": entity.lang,
            "type": entity.type,
            "matched_name": matched_name,
            "match": "exact",
        }

    def resolve_batch(self, names: List[str]) -> List[Dict[str, Any]]:
        """批量解析：每个输入名字返回 found/canonical 等。"""
        results = []
        for name in names:
            hit = self.resolve(name)
            results.append({
                "name": name,
                "found": hit is not None,
                **(hit or {}),
            })
        return results

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
                "score": hit["score"],
                "source": "vector",
            }
        return list(by_key.values())

    # ------------------------------------------------------------------ CRUD

    def create_entity(self, canonical: str, *, lang: str = "zh",
                      type: str = "other",
                      aliases: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        entity = Entity.new(
            canonical, lang=lang, type=type,
            aliases=[AliasEntry.from_dict(a) for a in (aliases or [])])
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
            entity = self.store.get(entity_id)
            if entity is None:
                raise AliasError(f"实体不存在：{entity_id}", "not_found")
            old_names = [e.name for e in entity.all_names()]
            if "canonical" in fields:
                entity.canonical = str(fields["canonical"]).strip()
            if "lang" in fields:
                entity.lang = str(fields["lang"]).strip().lower()
            if "type" in fields:
                entity.type = str(fields["type"]).strip().lower()
            if "aliases" in fields:
                entity.aliases = [AliasEntry.from_dict(a) for a in fields["aliases"]]
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
