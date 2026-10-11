"""情报主时间选择。

业务时间优先使用带有效时刻的来源发布时间；只有日期（00:00:00）的发布时间按低精度
时间处理，改用采集时间；归档时间只作最后兜底。归档时间本身仍用于处理审计、恢复和
运维统计。
"""

from __future__ import annotations

import datetime
import time
from typing import Any, Mapping

from Tools.DateTimeUtility import ensure_timezone_aware, time_str_to_datetime
from ServiceComponent.intelligence_contracts import ContentTimeSource


def publication_time_has_clock(value: Any) -> bool:
    """发布时间是否包含非零时刻；午夜值按“只有日期”处理。"""

    if value is None or value == "" or isinstance(value, datetime.date) \
            and not isinstance(value, datetime.datetime):
        return False

    parsed: datetime.datetime | None
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, time.struct_time):
        parsed = datetime.datetime(*value[:6])
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.datetime.fromtimestamp(float(value), tz=datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return False
    elif isinstance(value, str):
        parsed = time_str_to_datetime(value)
    else:
        return False

    if parsed is None:
        return False
    return any((parsed.hour, parsed.minute, parsed.second, parsed.microsecond))


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
    """选择带时刻的发布时间、采集时间或归档时间，并返回选择来源。"""

    raw = raw_data or {}
    raw_pub_time = raw.get("pub_time")
    if publication_time_has_clock(raw_pub_time):
        if value := normalize_intelligence_datetime(raw_pub_time):
            return value, ContentTimeSource.PUBLISHED
    if value := normalize_intelligence_datetime(raw.get("collect_time")):
        return value, ContentTimeSource.COLLECTED
    if value := normalize_intelligence_datetime(archived_at):
        return value, ContentTimeSource.ARCHIVED
    raise ValueError("情报缺少可解析的发布时间、采集时间和归档时间")


def document_content_time(document: Mapping[str, Any]) -> datetime.datetime | None:
    """读取已物化主时间；旧文档按相同规则回退。"""

    raw_data = document.get("raw_data") or {}
    if value := normalize_intelligence_datetime(document.get("content_time")):
        raw_pub_time = raw_data.get("pub_time")
        if document.get("content_time_source") != ContentTimeSource.PUBLISHED \
                or raw_pub_time is None \
                or publication_time_has_clock(raw_pub_time):
            return value
    try:
        return resolve_content_time(raw_data, document.get("archived_at"))[0]
    except ValueError:
        return None
