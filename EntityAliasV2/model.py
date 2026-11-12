"""EntityAlias V2 的领域对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


NAME_KINDS = frozenset({"canonical", "localized", "alias"})
NAME_ROLES = frozenset({"", "abbr", "full", "former", "metonym"})
RESOLUTION_MODES = frozenset({"direct", "context_required"})
ENTITY_STATUSES = frozenset({"unverified", "verified"})
RESOLUTION_STATUSES = frozenset({
    "resolved",
    "ambiguous",
    "type_conflict",
    "context_required",
    "unverified",
    "not_found",
    "invalid_type",
})


class AliasV2Error(Exception):
    """V2 业务错误；``code`` 可供未来 HTTP 层映射状态码。"""

    def __init__(self, message: str, code: str = "bad_request"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EntityName:
    name: str
    normalized_name: str
    lang: str = ""
    kind: str = "alias"
    role: str = ""
    resolution: str = "direct"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "normalized_name": self.normalized_name,
            "lang": self.lang,
            "kind": self.kind,
            "role": self.role,
            "resolution": self.resolution,
        }


@dataclass(frozen=True)
class EntityRecord:
    id: str
    canonical_type: str
    status: str
    description: str
    native_lang: str
    created_at: str
    updated_at: str
    names: tuple[EntityName, ...] = ()
    refs: dict[str, str] = field(default_factory=dict)

    @property
    def canonical(self) -> EntityName:
        for item in self.names:
            if item.kind == "canonical":
                return item
        raise AliasV2Error(f"实体 {self.id} 缺少 canonical 名称", "corrupt_data")

    def display_name(self, lang: str = "") -> str:
        requested = str(lang or "").strip().casefold()
        if requested == "native":
            requested = self.native_lang
        if requested:
            for item in self.names:
                if item.kind == "localized" and item.lang == requested:
                    return item.name
            if self.canonical.lang == requested:
                return self.canonical.name
        return self.canonical.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical": self.canonical.name,
            "canonical_lang": self.canonical.lang,
            "canonical_type": self.canonical_type,
            "status": self.status,
            "description": self.description,
            "native_lang": self.native_lang,
            "names": [item.to_dict() for item in self.names],
            "refs": dict(self.refs),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ResolutionCandidate:
    entity_id: str
    canonical: str
    canonical_type: str
    entity_status: str
    matched_name: str
    matched_kind: str
    resolution: str
    display: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "canonical": self.canonical,
            "canonical_type": self.canonical_type,
            "entity_status": self.entity_status,
            "matched_name": self.matched_name,
            "matched_kind": self.matched_kind,
            "resolution": self.resolution,
            "display": self.display,
        }


@dataclass(frozen=True)
class ResolutionResult:
    status: str
    name: str
    mention_type: str | None
    canonical_type: str | None
    candidates: tuple[ResolutionCandidate, ...] = ()
    other_type_candidates: tuple[ResolutionCandidate, ...] = ()
    reason: str = ""

    @property
    def resolved(self) -> ResolutionCandidate | None:
        return self.candidates[0] if self.status == "resolved" else None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "status": self.status,
            "name": self.name,
            "mention_type": self.mention_type,
            "canonical_type": self.canonical_type,
            "candidates": [item.to_dict() for item in self.candidates],
            "other_type_candidates": [
                item.to_dict() for item in self.other_type_candidates
            ],
        }
        if self.reason:
            result["reason"] = self.reason
        if self.resolved is not None:
            result["resolved"] = self.resolved.to_dict()
        return result
