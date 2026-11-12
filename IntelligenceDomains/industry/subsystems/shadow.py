"""行业领域 Shadow 配置。"""

from __future__ import annotations

from typing import Any, Mapping

from IntelligenceDomains.contracts import DomainShadowSettings


class IndustryShadowSupport:
    @staticmethod
    def from_config(config: Mapping[str, Any] | None) -> DomainShadowSettings:
        raw = dict((config or {}).get("shadow") or {})
        enabled = bool(raw.get("enabled", False))
        versions = tuple(int(item) for item in raw.get("versions", ()))
        sample_rate = max(0.0, min(1.0, float(raw.get("sample_rate", 0.0))))
        mode = str(raw.get("comparison_mode") or "same_model").strip()
        if mode not in {"same_model", "different_model", "replicate"}:
            raise ValueError(f"unsupported industry shadow comparison_mode: {mode!r}")
        if enabled and not versions:
            raise ValueError("industry shadow enabled but versions is empty")
        return DomainShadowSettings(
            enabled=enabled,
            versions=versions,
            sample_rate=sample_rate,
            comparison_mode=mode,
            client_group=str(raw.get("client_group") or "").strip() or None,
            experiment_id=str(raw.get("experiment_id") or "").strip(),
        )
