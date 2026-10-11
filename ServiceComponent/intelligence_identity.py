"""情报内部 UUID 的边界规范化。"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4


def parse_intelligence_uuid(value: Any) -> UUID | None:
    """解析外部 UUID；缺失或格式非法时返回 None。"""

    try:
        return UUID(str(value or "").strip())
    except (ValueError, TypeError, AttributeError):
        return None


def normalize_or_assign_intelligence_uuid(value: Any) -> UUID:
    """沿用合法 UUID，否则分配新的随机内部 UUID。"""

    return parse_intelligence_uuid(value) or uuid4()
