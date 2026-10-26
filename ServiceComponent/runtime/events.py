"""Hub 运行时使用的领域无关事件。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True, init=False)
class HubEvent:
    """在流程阶段之间传递的不可变信封。

    ``payload`` 故意保持为 ``Any``。不同情报域可以拥有完全不同的输入、AI
    输出和归档结构；运行时只负责投递，不校验也不改写领域数据。
    """

    event_type: str
    payload: Any = None
    domain_name: Optional[str] = None
    correlation_id: Optional[str] = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __init__(
        self, event_type: str, payload: Any = None,
        domain_name: Optional[str] = None,
        correlation_id: Optional[str] = None,
        event_id: Optional[str] = None,
        created_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *, subsystem: Optional[str] = None,
    ):
        """``subsystem`` 仅兼容旧调用；内部唯一事实源是 ``domain_name``。"""
        if domain_name and subsystem and domain_name != subsystem:
            raise ValueError("domain_name and legacy subsystem disagree")
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "domain_name", domain_name or subsystem)
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "event_id", event_id or str(uuid.uuid4()))
        object.__setattr__(self, "created_at", time.time() if created_at is None else created_at)
        object.__setattr__(self, "metadata", dict(metadata or {}))

    @property
    def subsystem(self) -> Optional[str]:
        """历史只读别名；新代码必须使用 ``domain_name``。"""
        return self.domain_name

    def derive(
            self,
            event_type: str,
            payload: Any = None,
            *,
            domain_name: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
            subsystem: Optional[str] = None,
    ) -> "HubEvent":
        """为后续阶段创建事件，并沿用关联 ID 与默认情报域。"""
        if domain_name and subsystem and domain_name != subsystem:
            raise ValueError("domain_name and legacy subsystem disagree")
        routed_domain = domain_name or subsystem or self.domain_name
        return HubEvent(
            event_type=event_type,
            payload=payload,
            domain_name=routed_domain,
            correlation_id=self.correlation_id or self.event_id,
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class HandlerFailure:
    """处理器异常的只读记录，不把异常对象跨线程传递。"""

    event_id: str
    event_type: str
    handler_name: str
    exception_type: str
    message: str
