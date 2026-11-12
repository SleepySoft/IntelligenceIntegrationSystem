"""EntityAlias V2 服务门面。

V2 只负责类型化名称候选与保守的确定性解析。文章角色、谓词和正文等上下文证据属于
EntityResolution；本服务在需要上下文时明确弃权，不自行猜测目标。
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import uuid
from dataclasses import replace
from typing import Any, Iterable

from .model import (
    ENTITY_STATUSES,
    NAME_KINDS,
    NAME_ROLES,
    RESOLUTION_MODES,
    AliasV2Error,
    EntityName,
    EntityRecord,
    ResolutionCandidate,
    ResolutionResult,
)
from .normalize import display_normalize, normalize_name
from .store import SQLiteAliasV2Store
from .types import normalize_entity_type


class EntityAliasV2Service:
    def __init__(self, store: SQLiteAliasV2Store):
        self.store = store

    def create_entity(
        self,
        canonical: str,
        *,
        canonical_type: str,
        canonical_lang: str = "",
        localized: dict[str, str] | None = None,
        aliases: Iterable[dict[str, Any]] = (),
        status: str = "unverified",
        description: str = "",
        native_lang: str = "",
        refs: dict[str, str] | None = None,
        entity_id: str | None = None,
    ) -> dict[str, Any]:
        entity_type = self._canonical_type(canonical_type)
        status = self._status(status)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        names = [self._name(
            canonical, lang=canonical_lang, kind="canonical", resolution="direct")]
        names.extend(self._name(
            name, lang=lang, kind="localized", resolution="direct")
            for lang, name in (localized or {}).items())
        names.extend(self._name(
            item.get("name", ""),
            lang=item.get("lang", ""),
            kind="alias",
            role=item.get("role", ""),
            resolution=item.get("resolution", "direct"),
        ) for item in aliases)
        self._validate_name_set(names)
        clean_refs = self._refs(refs or {})
        try:
            entity = self.store.insert_entity(
                entity_id=entity_id or "ent2_" + uuid.uuid4().hex[:16],
                canonical_type=entity_type,
                status=status,
                description=str(description or "").strip(),
                native_lang=str(native_lang or "").strip().casefold(),
                created_at=now,
                updated_at=now,
                names=names,
                refs=clean_refs,
            )
        except sqlite3.IntegrityError as exc:
            raise AliasV2Error(f"实体名称或外部引用冲突：{exc}", "conflict") from exc
        return entity.to_dict()

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        entity = self.store.get_entity(str(entity_id or "").strip())
        return entity.to_dict() if entity else None

    def delete_entity(self, entity_id: str) -> bool:
        return self.store.delete_entity(str(entity_id or "").strip())

    def list_entities(
        self, *, canonical_type: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_type = None
        if canonical_type is not None:
            normalized_type = self._canonical_type(canonical_type)
        if not 1 <= int(limit) <= 1000:
            raise AliasV2Error("limit 必须在 1..1000 之间")
        return [item.to_dict() for item in self.store.list_entities(
            canonical_type=normalized_type, limit=int(limit))]

    def add_alias(
        self,
        entity_id: str,
        name: str,
        *,
        lang: str = "",
        role: str = "",
        resolution: str = "direct",
    ) -> dict[str, Any]:
        entity = self._entity(entity_id)
        item = self._name(
            name, lang=lang, role=role, kind="alias", resolution=resolution)
        return self._replace(entity, names=(*entity.names, item)).to_dict()

    def remove_name(self, entity_id: str, name: str) -> dict[str, Any]:
        entity = self._entity(entity_id)
        key = normalize_name(name)
        target = next((item for item in entity.names
                       if item.normalized_name == key), None)
        if target is None:
            raise AliasV2Error(f"名称不存在：{name}", "not_found")
        if target.kind == "canonical":
            raise AliasV2Error("不能删除 canonical 名称；请先提升另一个名称")
        return self._replace(
            entity,
            names=tuple(item for item in entity.names
                        if item.normalized_name != key),
        ).to_dict()

    def set_canonical(self, entity_id: str, name: str) -> dict[str, Any]:
        entity = self._entity(entity_id)
        key = normalize_name(name)
        promoted = next((item for item in entity.names
                         if item.normalized_name == key), None)
        if promoted is None:
            raise AliasV2Error("只能提升实体已有的名称")
        if promoted.kind == "canonical":
            return entity.to_dict()
        names = []
        for item in entity.names:
            if item.kind == "canonical":
                names.append(replace(item, kind="alias"))
            elif item.normalized_name == key:
                names.append(replace(
                    item, kind="canonical", role="", resolution="direct"))
            else:
                names.append(item)
        return self._replace(entity, names=tuple(names)).to_dict()

    def update_entity(
        self,
        entity_id: str,
        *,
        canonical_type: str | None = None,
        status: str | None = None,
        description: str | None = None,
        native_lang: str | None = None,
        refs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        entity = self._entity(entity_id)
        return self._replace(
            entity,
            canonical_type=(
                self._canonical_type(canonical_type)
                if canonical_type is not None else entity.canonical_type
            ),
            status=self._status(status) if status is not None else entity.status,
            description=(
                str(description or "").strip()
                if description is not None else entity.description
            ),
            native_lang=(
                str(native_lang or "").strip().casefold()
                if native_lang is not None else entity.native_lang
            ),
            refs=self._refs(refs) if refs is not None else entity.refs,
        ).to_dict()

    def lookup_candidates(
        self, name: str, *, mention_type: str | None = None, lang: str = "",
    ) -> tuple[ResolutionCandidate, ...]:
        key = normalize_name(name)
        if not key:
            return ()
        canonical_type = None
        if mention_type is not None:
            canonical_type = normalize_entity_type(mention_type)
            if canonical_type is None:
                return ()
        return tuple(
            self._candidate(entity, matched, lang=lang)
            for entity, matched in self.store.find_by_name(
                key, canonical_type=canonical_type)
        )

    def resolve(
        self,
        name: str,
        *,
        mention_type: str | None,
        lang: str = "",
        allow_unverified: bool = False,
    ) -> ResolutionResult:
        """保守精确解析；任何歧义、类型冲突或上下文要求都会明确弃权。"""

        original_type = (
            str(mention_type).strip() if mention_type is not None else None)
        if mention_type is None or not original_type:
            candidates = self.lookup_candidates(name, lang=lang)
            return ResolutionResult(
                status="ambiguous" if candidates else "not_found",
                name=str(name or ""),
                mention_type=original_type,
                canonical_type=None,
                candidates=candidates,
                reason=(
                    "缺少 mention_type，只能返回名称候选"
                    if candidates else "名称未登记"
                ),
            )

        canonical_type = normalize_entity_type(original_type)
        if canonical_type is None:
            return ResolutionResult(
                status="invalid_type",
                name=str(name or ""),
                mention_type=original_type,
                canonical_type=None,
                reason="mention_type 无法规范化为 V2 canonical type",
            )

        typed = self.lookup_candidates(
            name, mention_type=canonical_type, lang=lang)
        if not typed:
            other = self.lookup_candidates(name, lang=lang)
            if other:
                return ResolutionResult(
                    status="type_conflict",
                    name=str(name or ""),
                    mention_type=original_type,
                    canonical_type=canonical_type,
                    other_type_candidates=other,
                    reason="名称存在，但没有类型兼容的候选",
                )
            return ResolutionResult(
                status="not_found",
                name=str(name or ""),
                mention_type=original_type,
                canonical_type=canonical_type,
                reason="名称未登记",
            )

        if len(typed) > 1:
            return ResolutionResult(
                status="ambiguous",
                name=str(name or ""),
                mention_type=original_type,
                canonical_type=canonical_type,
                candidates=typed,
                reason="同名同类型存在多个候选",
            )

        candidate = typed[0]
        if candidate.resolution == "context_required":
            return ResolutionResult(
                status="context_required",
                name=str(name or ""),
                mention_type=original_type,
                canonical_type=canonical_type,
                candidates=typed,
                reason="该名称绑定明确要求上下文消歧",
            )
        if candidate.entity_status != "verified" and not allow_unverified:
            return ResolutionResult(
                status="unverified",
                name=str(name or ""),
                mention_type=original_type,
                canonical_type=canonical_type,
                candidates=typed,
                reason="唯一候选尚未核实",
            )
        return ResolutionResult(
            status="resolved",
            name=str(name or ""),
            mention_type=original_type,
            canonical_type=canonical_type,
            candidates=typed,
        )

    def expand_names(
        self, name: str, *, mention_type: str,
    ) -> list[str]:
        """仅对唯一、已核实、可直接解析的实体展开名称。"""

        result = self.resolve(name, mention_type=mention_type)
        if result.resolved is None:
            return [name]
        entity = self._entity(result.resolved.entity_id)
        return [item.name for item in entity.names]

    def status(self) -> dict[str, Any]:
        return {
            "version": 2,
            "storage": "sqlite",
            "path": self.store.path,
            "entities": self.store.count_entities(),
            "names": self.store.count_names(),
            "v1_imported": False,
        }

    def _replace(
        self,
        entity: EntityRecord,
        *,
        canonical_type: str | None = None,
        status: str | None = None,
        description: str | None = None,
        native_lang: str | None = None,
        names: tuple[EntityName, ...] | None = None,
        refs: dict[str, str] | None = None,
    ) -> EntityRecord:
        final_names = names if names is not None else entity.names
        self._validate_name_set(final_names)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            return self.store.replace_entity(
                entity.id,
                canonical_type=canonical_type or entity.canonical_type,
                status=status or entity.status,
                description=(
                    entity.description if description is None else description),
                native_lang=(
                    entity.native_lang if native_lang is None else native_lang),
                updated_at=now,
                names=final_names,
                refs=entity.refs if refs is None else refs,
            )
        except sqlite3.IntegrityError as exc:
            raise AliasV2Error(f"实体名称或外部引用冲突：{exc}", "conflict") from exc

    def _entity(self, entity_id: str) -> EntityRecord:
        entity = self.store.get_entity(str(entity_id or "").strip())
        if entity is None:
            raise AliasV2Error(f"实体不存在：{entity_id}", "not_found")
        return entity

    @staticmethod
    def _candidate(
        entity: EntityRecord, matched: EntityName, *, lang: str,
    ) -> ResolutionCandidate:
        return ResolutionCandidate(
            entity_id=entity.id,
            canonical=entity.canonical.name,
            canonical_type=entity.canonical_type,
            entity_status=entity.status,
            matched_name=matched.name,
            matched_kind=matched.kind,
            resolution=matched.resolution,
            display=entity.display_name(lang),
        )

    @staticmethod
    def _canonical_type(value: str) -> str:
        result = normalize_entity_type(value)
        if result is None:
            raise AliasV2Error(f"未知 canonical type：{value!r}", "invalid_type")
        return result

    @staticmethod
    def _status(value: str) -> str:
        result = str(value or "").strip().casefold()
        if result not in ENTITY_STATUSES:
            raise AliasV2Error(f"非法实体状态：{value!r}")
        return result

    @staticmethod
    def _name(
        value: str,
        *,
        lang: str = "",
        kind: str,
        role: str = "",
        resolution: str = "direct",
    ) -> EntityName:
        name = display_normalize(value)
        key = normalize_name(name)
        kind = str(kind or "").strip().casefold()
        role = str(role or "").strip().casefold()
        resolution = str(resolution or "").strip().casefold()
        if not name or not key:
            raise AliasV2Error("名称不能为空")
        if kind not in NAME_KINDS:
            raise AliasV2Error(f"非法名称种类：{kind!r}")
        if role not in NAME_ROLES:
            raise AliasV2Error(f"非法别名角色：{role!r}")
        if resolution not in RESOLUTION_MODES:
            raise AliasV2Error(f"非法解析模式：{resolution!r}")
        if kind != "alias" and role:
            raise AliasV2Error("只有 alias 名称可以声明 role")
        if kind != "alias" and resolution != "direct":
            raise AliasV2Error("canonical/localized 名称必须是 direct")
        return EntityName(
            name=name,
            normalized_name=key,
            lang=str(lang or "").strip().casefold(),
            kind=kind,
            role=role,
            resolution=resolution,
        )

    @staticmethod
    def _validate_name_set(names: Iterable[EntityName]) -> None:
        items = tuple(names)
        canonical = [item for item in items if item.kind == "canonical"]
        if len(canonical) != 1:
            raise AliasV2Error("每个实体必须且只能有一个 canonical 名称")
        keys = [item.normalized_name for item in items]
        if len(keys) != len(set(keys)):
            raise AliasV2Error("同一实体内名称归一化后不能重复", "conflict")
        localized_langs = [item.lang for item in items if item.kind == "localized"]
        if any(not lang for lang in localized_langs):
            raise AliasV2Error("localized 名称必须声明语言")
        if len(localized_langs) != len(set(localized_langs)):
            raise AliasV2Error("同一实体每种语言只能有一个 localized 名称")

    @staticmethod
    def _refs(values: dict[str, str]) -> dict[str, str]:
        result = {}
        for key, value in values.items():
            namespace = str(key or "").strip().casefold()
            identifier = str(value or "").strip()
            if not namespace or not identifier:
                raise AliasV2Error("外部引用的 namespace/value 不能为空")
            result[namespace] = identifier
        return result
