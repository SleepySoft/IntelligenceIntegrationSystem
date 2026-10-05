"""Hub 运行时使用的领域无关事件。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class HubEvent:
    """在流程阶段之间传递的不可变信封。

    ``payload`` 故意保持为 ``Any``。不同子系统可以拥有完全不同的输入、AI
    输出和归档结构；运行时只负责投递，不校验也不改写领域数据。
    """

    event_type: str
    payload: Any = None
    subsystem: Optional[str] = None
    correlation_id: Optional[str] = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def derive(
            self,
            event_type: str,
            payload: Any = None,
            *,
            subsystem: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
    ) -> "HubEvent":
        """为后续阶段创建事件，并沿用关联 ID 与默认子系统。"""
        return HubEvent(
            event_type=event_type,
            payload=payload,
            subsystem=self.subsystem if subsystem is None else subsystem,
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
