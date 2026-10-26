"""实体指称解析服务：按 EntityRef 指定目标，保留审计与撤销能力。"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from EntityAlias.normalize import normalize_name

from .model import EntityRef, ResolutionError


@dataclass(frozen=True)
class ResolutionScope:
    store: Any
    reference_loader: Callable[[EntityRef], dict[str, Any] | None]


class EntityResolutionService:
    MAX_BATCH_SIZE = 200

    def __init__(self, scopes: dict[str, ResolutionScope], *, entity_registry=None):
        self.scopes = dict(scopes)
        self.entity_registry = entity_registry
        self._lock = threading.RLock()

    def _scope(self, domain_name: str) -> ResolutionScope:
        scope = self.scopes.get(str(domain_name or "").strip())
        if scope is None:
            raise ResolutionError(f"未知情报域：{domain_name}", "not_found")
        return scope

    def _load_reference(self, ref: EntityRef) -> dict[str, Any]:
        value = self._scope(ref.domain_name).reference_loader(ref)
        if value is None:
            raise ResolutionError("情报实体引用不存在或已过期", "not_found")
        return value

    def get_reference(self, ref: EntityRef) -> dict[str, Any]:
        """返回权威抽取中的实体快照，供受保护的编辑接口预览/登记别名。"""
        return dict(self._load_reference(ref))

    def resolve_many(self, values: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        requests = list(values)
        refs = [EntityRef.from_mapping(item) for item in requests]
        by_scope: dict[str, list[EntityRef]] = {}
        for ref in refs:
            by_scope.setdefault(ref.domain_name, []).append(ref)
        resolved: dict[str, dict[str, Any]] = {}
        for domain_name, items in by_scope.items():
            resolved.update(self._scope(domain_name).store.resolve_many(items))

        for request, ref in zip(requests, refs):
            hit = resolved.get(ref.key)
            if hit and hit["kind"] == "global":
                entity = self._global_entity(hit["target_id"])
                if entity:
                    hit.update({
                        "name": entity.get("canonical") or hit["target_id"],
                        "type": entity.get("type") or request.get("type") or "other",
                        "status": entity.get("status"),
                    })
                continue
            if hit:
                continue
            surface = str(request.get("surface") or "").strip()
            exact = self.entity_registry.resolve(surface) if self.entity_registry and surface else None
            if exact:
                resolved[ref.key] = {
                    "kind": "global",
                    "target_id": exact["entity_id"],
                    "name": exact["canonical"],
                    "type": exact.get("type") or request.get("type") or "other",
                    "status": exact.get("status"),
                    "method": "fixed_alias",
                }
        return resolved

    def assign_global(self, ref: EntityRef, entity_id: str, *, actor: str,
                      reason: str = "") -> dict[str, Any]:
        entity = self._global_entity(entity_id)
        if entity is None:
            raise ResolutionError(f"全局实体不存在：{entity_id}", "not_found")
        return self._assign(ref, "global", entity_id, actor=actor, reason=reason)

    def assign_contextual(self, ref: EntityRef, contextual_entity_id: str, *,
                          actor: str, reason: str = "") -> dict[str, Any]:
        scope = self._scope(ref.domain_name)
        if scope.store.get_contextual(contextual_entity_id) is None:
            raise ResolutionError(f"上下文实体不存在：{contextual_entity_id}", "not_found")
        return self._assign(
            ref, "contextual", contextual_entity_id, actor=actor, reason=reason)

    def assign_many(self, refs: Iterable[EntityRef], target_kind: str,
                    target_id: str, *, actor: str,
                    reason: str = "") -> dict[str, Any]:
        """把多个具体指称批量指向同一目标；全局实体本身仍独立于情报。"""
        items = self._validated_batch(refs)
        target_kind = str(target_kind or "").strip()
        target_id = str(target_id or "").strip()
        if target_kind == "global":
            if self._global_entity(target_id) is None:
                raise ResolutionError(f"全局实体不存在：{target_id}", "not_found")
        elif target_kind == "contextual":
            for domain_name in {ref.domain_name for ref in items}:
                if self._scope(domain_name).store.get_contextual(target_id) is None:
                    raise ResolutionError(f"上下文实体不存在：{target_id}", "not_found")
        else:
            raise ResolutionError("target_kind 必须是 global 或 contextual")
        # 先验证全部引用，避免因后一个引用无效造成可预见的半批写入。
        for ref in items:
            self._load_reference(ref)
        results = [
            self._assign(ref, target_kind, target_id, actor=actor, reason=reason)
            for ref in items
        ]
        return {
            "changed": sum(bool(item.get("changed")) for item in results),
            "total": len(results),
            "results": results,
        }

    def _assign(self, ref: EntityRef, kind: str, target_id: str, *,
                actor: str, reason: str) -> dict[str, Any]:
        source = self._load_reference(ref)
        store = self._scope(ref.domain_name).store
        with self._lock:
            before = store.get_assignment(ref)
            if before and before.get("kind") == kind and before.get("target_id") == target_id:
                return {"changed": False, "assignment": before}
            decision_id = "decision_" + uuid.uuid4().hex
            after = {
                "kind": kind,
                "target_id": target_id,
                "surface": source.get("name") or "",
                "normalized_surface": normalize_name(source.get("name") or ""),
                "method": "manual",
                "confidence": 1.0,
                "decision_id": decision_id,
            }
            store.apply_assignment(ref, after)
            store.insert_audit({
                "decision_id": decision_id,
                "ref_key": ref.key,
                "ref": ref.to_dict(),
                "before": before,
                "after": {"kind": kind, "target_id": target_id},
                "actor": str(actor or "unknown"),
                "reason": str(reason or "").strip(),
                "created_at": datetime.now(timezone.utc),
            })
        return {"changed": True, "decision_id": decision_id,
                "assignment": {"kind": kind, "target_id": target_id}}

    def unassign(self, ref: EntityRef, *, actor: str, reason: str = "") -> dict[str, Any]:
        self._load_reference(ref)
        store = self._scope(ref.domain_name).store
        with self._lock:
            before = store.get_assignment(ref)
            if before is None:
                return {"changed": False, "assignment": None}
            decision_id = "decision_" + uuid.uuid4().hex
            store.apply_assignment(ref, None)
            store.insert_audit({
                "decision_id": decision_id,
                "ref_key": ref.key,
                "ref": ref.to_dict(),
                "before": before,
                "after": None,
                "actor": str(actor or "unknown"),
                "reason": str(reason or "").strip(),
                "created_at": datetime.now(timezone.utc),
            })
        return {"changed": True, "decision_id": decision_id, "assignment": None}

    def unassign_many(self, refs: Iterable[EntityRef], *, actor: str,
                      reason: str = "") -> dict[str, Any]:
        items = self._validated_batch(refs)
        for ref in items:
            self._load_reference(ref)
        results = [self.unassign(ref, actor=actor, reason=reason) for ref in items]
        return {
            "changed": sum(bool(item.get("changed")) for item in results),
            "total": len(results),
            "results": results,
        }

    def undo(self, domain_name: str, decision_id: str, *, actor: str) -> dict[str, Any]:
        store = self._scope(domain_name).store
        with self._lock:
            audit = store.get_audit(decision_id)
            if audit is None:
                raise ResolutionError("解析决定不存在", "not_found")
            if audit.get("undone_at"):
                return {"changed": False, "decision_id": decision_id}
            ref = EntityRef.from_mapping(audit["ref"])
            current = store.get_assignment(ref)
            expected = audit.get("after")
            if self._target(current) != self._target(expected):
                raise ResolutionError("该指称之后已被重新指定，不能直接撤销旧决定", "conflict")
            before = audit.get("before")
            if before:
                source = self._load_reference(ref)
                before = {
                    **before,
                    "surface": source.get("name") or "",
                    "normalized_surface": normalize_name(source.get("name") or ""),
                    "method": "undo",
                    "confidence": 1.0,
                    "decision_id": before.get("decision_id"),
                }
            store.apply_assignment(ref, before)
            store.mark_undone(decision_id, actor=str(actor or "unknown"))
        return {"changed": True, "decision_id": decision_id,
                "assignment": before}

    def create_contextual(self, domain_name: str, name: str, *, actor: str,
                          optional_fields: dict[str, Any] | None = None) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise ResolutionError("上下文实体名称不能为空")
        now = datetime.now(timezone.utc)
        document = {
            "id": "ctx_" + uuid.uuid4().hex[:16],
            "name": name,
            "type": str((optional_fields or {}).get("type") or "other"),
            "created_by": str(actor or "unknown"),
            "created_at": now,
            "updated_at": now,
        }
        for field in ("countries", "locations", "time_range", "topic", "description"):
            value = (optional_fields or {}).get(field)
            if value not in (None, "", [], {}):
                document[field] = value
        return self._scope(domain_name).store.create_contextual(document)

    def search_contextual(self, domain_name: str, query: str, *, limit: int = 20):
        return self._scope(domain_name).store.search_contextual(query, limit=limit)

    def list_references(self, domain_name: str, target_id: str, *, limit: int = 200):
        return self._scope(domain_name).store.list_references(target_id, limit=limit)

    def _global_entity(self, entity_id: str) -> dict[str, Any] | None:
        if self.entity_registry is None:
            return None
        return self.entity_registry.get_entity(str(entity_id or "").strip())

    def _validated_batch(self, refs: Iterable[EntityRef]) -> list[EntityRef]:
        items = list(refs)
        if not items:
            raise ResolutionError("至少选择一个情报实体引用")
        if len(items) > self.MAX_BATCH_SIZE:
            raise ResolutionError(f"单次最多处理 {self.MAX_BATCH_SIZE} 个情报实体引用")
        unique: dict[str, EntityRef] = {}
        for ref in items:
            if not isinstance(ref, EntityRef):
                raise ResolutionError("无效的情报实体引用")
            unique.setdefault(ref.key, ref)
        return list(unique.values())

    @staticmethod
    def _target(value: dict[str, Any] | None):
        if not value:
            return None
        return value.get("kind"), value.get("target_id")
