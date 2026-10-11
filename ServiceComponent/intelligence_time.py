"""情报主时间选择。

业务时间优先使用来源发布时间；来源没有可解析的发布时间时使用采集时间；归档时间只作
最后兜底。归档时间本身仍用于处理审计、恢复和运维统计。
"""

from __future__ import annotations

import datetime
import time
from typing import Any, Literal, Mapping

from Tools.DateTimeUtility import ensure_timezone_aware, time_str_to_datetime


ContentTimeSource = Literal["published", "collected", "archived"]


def normalize_intelligence_datetime(value: Any) -> datetime.datetime | None:
    """把常见时间输入规范为 UTC aware datetime；不可解析时返回 None。"""

    parsed: datetime.datetime | None
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, datetime.date):
        parsed = datetime.datetime.combine(value, datetime.time.min)
    elif isinstance(value, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(float(value), tz=datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, time.struct_time):
        try:
            parsed = datetime.datetime.fromtimestamp(time.mktime(value))
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        parsed = time_str_to_datetime(value)
    else:
        return None

    if parsed is None:
        return None
    return ensure_timezone_aware(parsed).astimezone(datetime.timezone.utc)


def resolve_content_time(
        raw_data: Mapping[str, Any] | None,
        archived_at: Any = None,
) -> tuple[datetime.datetime, ContentTimeSource]:
    """选择发布时间、采集时间或归档时间，并返回选择来源。"""

    raw = raw_data or {}
    for key, source in (
        ("pub_time", "published"),
        ("collect_time", "collected"),
    ):
        if value := normalize_intelligence_datetime(raw.get(key)):
            return value, source
    if value := normalize_intelligence_datetime(archived_at):
        return value, "archived"
    raise ValueError("情报缺少可解析的发布时间、采集时间和归档时间")


def document_content_time(document: Mapping[str, Any]) -> datetime.datetime | None:
    """读取已物化主时间；旧文档按相同规则回退。"""

    if value := normalize_intelligence_datetime(document.get("content_time")):
        return value
    try:
        return resolve_content_time(
            document.get("raw_data") or {}, document.get("archived_at"))[0]
    except ValueError:
        return None
