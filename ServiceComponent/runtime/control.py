"""运行时控制开关：暂停采集/分析等处理环节，用于维护与发布测试场景。

设计约定：
- 进程内存态，不持久化——重启即恢复全运行，避免"忘了关"造成停产；
- ``maintenance`` 是总开关，开启后所有 scope 视为暂停；
- 暂停语义是"不再消费/接收新工作"，队列积压由 max_inflight 等既有机制兜底；
- 每次切换写审计日志（操作人、scope、动作、时间）。
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 可暂停的处理环节。translation/vector 本期为预留位（扩展插件尚未接入检查）。
CONTROL_SCOPES = ("intake", "analysis", "shadow", "translation", "vector")
SCOPE_MAINTENANCE = "maintenance"
ALL_SCOPES = frozenset((*CONTROL_SCOPES, SCOPE_MAINTENANCE))


class RuntimeControlState:
    """线程安全的运行控制开关集合。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._paused: set[str] = set()
        self._backup_lease: dict[str, Any] | None = None
        self._active_intake = 0
        self._revision = 0
        self._lease_generation = 0

    def _expire_backup_lease_locked(self) -> None:
        lease = self._backup_lease
        if lease is not None and time.monotonic() >= lease["deadline"]:
            logger.warning("备份维护租约已过期，自动恢复 intake: lease_id=%s", lease["lease_id"])
            self._backup_lease = None
            self._revision += 1

    def is_paused(self, scope: str) -> bool:
        with self._lock:
            self._expire_backup_lease_locked()
            leased = self._backup_lease is not None and scope == "intake"
            return leased or SCOPE_MAINTENANCE in self._paused or scope in self._paused

    def try_enter_intake(self) -> bool:
        """原子地跨过 intake 闸门；与获取备份租约共用同一把锁。"""
        with self._lock:
            self._expire_backup_lease_locked()
            if (self._backup_lease is not None or SCOPE_MAINTENANCE in self._paused
                    or "intake" in self._paused):
                return False
            self._active_intake += 1
            return True

    def leave_intake(self) -> None:
        with self._lock:
            if self._active_intake <= 0:
                raise RuntimeError("intake 活跃计数下溢")
            self._active_intake -= 1

    def acquire_backup_lease(self, ttl_seconds: float, operator: str = "backup") -> dict[str, Any]:
        """短时封闭 intake；租约过期后自动失效，避免备份异常导致永久停收。"""

        ttl = float(ttl_seconds)
        if ttl < 10 or ttl > 3600:
            raise ValueError("备份维护租约 ttl_seconds 必须在 10..3600 之间")
        with self._lock:
            self._expire_backup_lease_locked()
            if self._backup_lease is not None:
                raise RuntimeError("已有备份维护租约")
            now = time.time()
            self._lease_generation += 1
            lease = {
                "lease_id": secrets.token_urlsafe(24),
                "operator": operator or "backup",
                "acquired_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "expires_at": datetime.fromtimestamp(now + ttl, timezone.utc).isoformat(),
                "deadline": time.monotonic() + ttl,
                "ttl_seconds": ttl,
                "generation": self._lease_generation,
            }
            self._backup_lease = lease
            self._revision += 1
            logger.info("已获取备份维护租约，暂停 intake: lease_id=%s ttl=%ss",
                        lease["lease_id"], ttl)
            return self._public_lease(lease)

    def renew_backup_lease(self, lease_id: str, ttl_seconds: float) -> dict[str, Any]:
        ttl = float(ttl_seconds)
        if ttl < 10 or ttl > 3600:
            raise ValueError("备份维护租约 ttl_seconds 必须在 10..3600 之间")
        with self._lock:
            self._expire_backup_lease_locked()
            lease = self._require_backup_lease_locked(lease_id)
            now = time.time()
            lease["expires_at"] = datetime.fromtimestamp(now + ttl, timezone.utc).isoformat()
            lease["deadline"] = time.monotonic() + ttl
            lease["ttl_seconds"] = ttl
            self._revision += 1
            return self._public_lease(lease)

    def release_backup_lease(self, lease_id: str) -> None:
        with self._lock:
            self._expire_backup_lease_locked()
            self._require_backup_lease_locked(lease_id)
            self._backup_lease = None
            self._revision += 1
        logger.info("已释放备份维护租约，恢复 intake: lease_id=%s", lease_id)

    def _require_backup_lease_locked(self, lease_id: str) -> dict[str, Any]:
        lease = self._backup_lease
        if lease is None or not secrets.compare_digest(
                str(lease["lease_id"]), str(lease_id or "")):
            raise KeyError("备份维护租约不存在或已过期")
        return lease

    @staticmethod
    def _public_lease(lease: dict[str, Any] | None) -> dict[str, Any] | None:
        if lease is None:
            return None
        return {key: value for key, value in lease.items() if key != "deadline"}

    def pause(self, scope: str, operator: str = "") -> None:
        self._set(scope, True, operator)

    def resume(self, scope: str, operator: str = "") -> None:
        self._set(scope, False, operator)

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._expire_backup_lease_locked()
            paused = set(self._paused)
            backup_lease = self._public_lease(self._backup_lease)
            active_intake = self._active_intake
            revision = self._revision
        return {
            "maintenance": SCOPE_MAINTENANCE in paused,
            "scopes": {
                scope: ((backup_lease is not None and scope == "intake")
                        or SCOPE_MAINTENANCE in paused or scope in paused)
                for scope in CONTROL_SCOPES
            },
            "explicitly_paused": sorted(paused),
            "backup_lease": backup_lease,
            "active_intake": active_intake,
            "revision": revision,
        }

    def _set(self, scope: str, paused: bool, operator: str) -> None:
        if scope not in ALL_SCOPES:
            raise ValueError(f"未知控制范围: {scope!r}（可选: {sorted(ALL_SCOPES)}）")
        with self._lock:
            changed = False
            if paused:
                changed = scope not in self._paused
                self._paused.add(scope)
            else:
                changed = scope in self._paused
                self._paused.discard(scope)
            if changed:
                self._revision += 1
        logger.info(
            "运行控制变更: %s %s（操作人: %s）",
            "暂停" if paused else "恢复", scope, operator or "unknown",
        )
