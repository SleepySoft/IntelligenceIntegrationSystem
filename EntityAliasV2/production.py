"""EntityAlias V2 的生产解析适配器与可回滚路由。

事实库仍保留候选的原始核实状态；这里单独决定某次指称是否有足够证据进入正式
EntityResolution，避免把“实体可信”和“这个简称可直接绑定”混成一个开关。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .normalize import normalize_name


@dataclass(frozen=True)
class ProductionPolicy:
    """首版自动绑定策略；宁可弃权，也不把模糊组织/地名自动合并。"""

    trust_wikidata: bool = True
    contextual_person_aliases: bool = True
    require_country_for_contextual_person: bool = True
    min_contextual_name_length: int = 2


class EntityAliasV2ProductionAdapter:
    """把 V2 类型化候选投影为 EntityResolution 的全局实体接口。"""

    def __init__(self, service, *, policy: ProductionPolicy | None = None):
        self.service = service
        self.policy = policy or ProductionPolicy()

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        entity = self.service.get_entity(entity_id)
        if not entity:
            return None
        return {
            **entity,
            "type": entity.get("canonical_type") or "other",
        }

    def resolve_reference(self, request: dict[str, Any]) -> dict[str, Any] | None:
        surface = str(request.get("surface") or "").strip()
        mention_type = str(request.get("type") or "").strip()
        if not surface or not mention_type:
            return None

        result = self.service.resolve(
            surface,
            mention_type=mention_type,
            lang=str(request.get("lang") or ""),
        )
        candidate = result.resolved
        method = "v2_direct"
        if candidate is None and len(result.candidates) == 1:
            candidate = result.candidates[0]
            entity = self.service.get_entity(candidate.entity_id)
            if not self._trusted_external(entity):
                return None
            if result.status == "unverified" and candidate.resolution == "direct":
                method = "v2_trusted_external"
            elif result.status == "context_required" and self._safe_contextual_person(
                    request, candidate):
                method = "v2_contextual_person"
            else:
                return None
        if candidate is None:
            return None
        return {
            "entity_id": candidate.entity_id,
            "canonical": candidate.display or candidate.canonical,
            "type": candidate.canonical_type,
            "status": candidate.entity_status,
            "method": method,
        }

    def _trusted_external(self, entity: dict[str, Any] | None) -> bool:
        return bool(
            self.policy.trust_wikidata
            and entity
            and str((entity.get("refs") or {}).get("wikidata") or "").startswith("Q")
        )

    def _safe_contextual_person(self, request, candidate) -> bool:
        if not self.policy.contextual_person_aliases:
            return False
        if candidate.canonical_type != "person":
            return False
        if len(normalize_name(candidate.matched_name)) < self.policy.min_contextual_name_length:
            return False
        if self.policy.require_country_for_contextual_person:
            country = str(request.get("country_code") or "").strip().upper()
            if len(country) != 2:
                return False
        return True


class EntityRegistryRouter:
    """按 shadow/prefer_v2/v2_only 切换正式解析，不改变两套事实库。"""

    def __init__(self, *, mode: str, v2=None, legacy=None):
        self.mode = mode
        self.v2 = v2
        self.legacy = legacy

    def resolve_reference(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if self.mode != "shadow" and self.v2 is not None:
            hit = self.v2.resolve_reference(request)
            if hit:
                return hit
        if self.mode != "v2_only" and self.legacy is not None:
            surface = str(request.get("surface") or "").strip()
            return self.legacy.resolve(surface) if surface else None
        return None

    def resolve(self, name: str):
        """兼容尚未传类型/上下文的旧调用；V2 不在缺少证据时猜测。"""
        if self.mode != "v2_only" and self.legacy is not None:
            return self.legacy.resolve(name)
        return None

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        if self.v2 is not None:
            entity = self.v2.get_entity(entity_id)
            if entity:
                return entity
        if self.mode != "v2_only" and self.legacy is not None:
            return self.legacy.get_entity(entity_id)
        return None
