"""通用时间位置与区间计算；日/月/年精度不冒充精确时刻。"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone

from ..ir import TimeExpression


def position(value: str | None) -> tuple[datetime, datetime] | None:
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{4}", value):
            lower = datetime(int(value), 1, 1, tzinfo=timezone.utc)
            return lower, lower.replace(year=lower.year + 1)
        if re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = map(int, value.split("-"))
            lower = datetime(year, month, 1, tzinfo=timezone.utc)
            return lower, lower + timedelta(days=calendar.monthrange(year, month)[1])
        lower = datetime.fromisoformat(value.replace("Z", "+00:00"))
        lower = lower.replace(tzinfo=timezone.utc) if lower.tzinfo is None else lower.astimezone(timezone.utc)
        extent = timedelta(days=1) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else timedelta(microseconds=1)
        return lower, lower + extent
    except (ValueError, OverflowError):
        return None


def expression_position(value: TimeExpression | None) -> tuple[datetime, datetime] | None:
    if value is None or value.approximate:
        return None
    normalized = value.normalized
    if normalized and value.precision == "year":
        normalized = normalized[:4]
    elif normalized and value.precision == "month":
        normalized = normalized[:7]
    elif normalized and value.precision == "day":
        normalized = normalized[:10]
    return position(normalized)


def effective_time(event, qualifier=None) -> datetime | None:
    # 有明确限定时间但无法解析时，不能拿报道时间冒充有效时间。
    if qualifier is not None and qualifier.time is not None:
        bounds = expression_position(qualifier.time)
        return bounds[0] if bounds else None
    for key in ("effective_time", "event_time", "start_time"):
        expression = event.time.get(key)
        if expression is not None:
            bounds = expression_position(expression)
            return bounds[0] if bounds else None
    return None


def event_bounds(event) -> tuple[str | None, str | None]:
    def value(*keys):
        for key in keys:
            expression = event.time.get(key)
            if expression is not None and expression_position(expression):
                value = expression.normalized
                width = {"year": 4, "month": 7, "day": 10}.get(expression.precision)
                return value[:width] if width else value
        return None
    start = value("start_time", "event_time", "effective_time")
    end = value("end_time", "event_time")
    return start, end
