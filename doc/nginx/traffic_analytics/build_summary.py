#!/usr/bin/env python3
"""把隐私收敛后的页面访问日志聚合为不含访客标识的轻量 JSON。"""

from __future__ import annotations

import collections
import datetime as dt
import gzip
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


LOG_DIR = Path("/var/log/iis-traffic")
OUTPUT = Path("/var/www/traffic-analytics/summary.json")
TIMEZONE = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")
LINE_RE = re.compile(
    r'^(?P<ip>\S+) \[(?P<time>[^]]+)] "(?P<method>\S+) (?P<path>\S+) (?P<protocol>[^"]+)" '
    r'(?P<status>\d{3}) (?P<bytes>\d+) "(?P<referrer>[^"]*)" "(?P<agent>[^"]*)" '
    r'(?P<duration>[0-9.]+) (?P<host>\S+) (?P<country>\S+)$')
BOT_RE = re.compile(
    r"bot|spider|crawler|slurp|preview|headless|curl|wget|python-requests|go-http-client|"
    r"httpclient|scanner|scrapy|facebookexternalhit|bingpreview|uptime|monitor",
    re.I,
)


def iter_lines():
    for path in sorted(LOG_DIR.glob("pageviews.log*")):
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as stream:
                yield from stream
        except OSError:
            continue


def browser(agent: str) -> str:
    for pattern, label in (
        (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"),
        (r"Firefox/", "Firefox"), (r"Chrome/|CriOS/", "Chrome"),
        (r"Safari/", "Safari"),
    ):
        if re.search(pattern, agent, re.I):
            return label
    return "其他"


def device(agent: str) -> str:
    if re.search(r"iPad|Tablet", agent, re.I):
        return "平板"
    if re.search(r"Mobile|Android|iPhone", agent, re.I):
        return "手机"
    return "桌面"


def source_name(referrer: str, host: str) -> str:
    if not referrer or referrer == "-":
        return "直接访问/未知"
    try:
        name = (urlsplit(referrer).hostname or "").lower()
    except ValueError:
        return "其他"
    if not name:
        return "其他"
    if name == host.lower() or name.endswith("." + host.lower()):
        return "站内跳转"
    return name


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return round(ordered[index] * 1000)


def new_bucket():
    return {
        "visitors": set(), "pageviews": 0, "crawler_requests": 0,
        "statuses": collections.Counter(), "durations": [],
        "pages": collections.Counter(), "sources": collections.Counter(),
        "countries": collections.Counter(), "devices": collections.Counter(),
        "browsers": collections.Counter(), "daily": collections.defaultdict(
            lambda: {"visitors": set(), "pageviews": 0, "crawler_requests": 0}),
    }


def finalize(bucket):
    pageviews = bucket["pageviews"]
    errors = sum(count for status, count in bucket["statuses"].items() if status >= 400)
    return {
        "visitors": len(bucket["visitors"]),
        "pageviews": pageviews,
        "pages_per_visitor": round(pageviews / len(bucket["visitors"]), 2) if bucket["visitors"] else 0,
        "crawler_requests": bucket["crawler_requests"],
        "error_rate": round(errors / pageviews, 4) if pageviews else 0,
        "latency_ms": {
            "average": round(sum(bucket["durations"]) / len(bucket["durations"]) * 1000) if bucket["durations"] else 0,
            "p50": percentile(bucket["durations"], 0.5),
            "p95": percentile(bucket["durations"], 0.95),
        },
        "daily": [
            {"date": day, "visitors": len(values["visitors"]),
             "pageviews": values["pageviews"], "crawler_requests": values["crawler_requests"]}
            for day, values in sorted(bucket["daily"].items())
        ],
        "top_pages": bucket["pages"].most_common(12),
        "sources": bucket["sources"].most_common(10),
        "countries": bucket["countries"].most_common(10),
        "devices": bucket["devices"].most_common(),
        "browsers": bucket["browsers"].most_common(8),
        "status_codes": [[str(status), count] for status, count in sorted(bucket["statuses"].items())],
    }


def main():
    now = dt.datetime.now(TIMEZONE)
    cutoffs = {days: (now.date() - dt.timedelta(days=days - 1)) for days in (1, 7, 30)}
    buckets = {days: new_bucket() for days in cutoffs}
    earliest = None
    invalid = 0
    for raw_line in iter_lines():
        match = LINE_RE.match(raw_line.rstrip("\n"))
        if not match:
            invalid += 1
            continue
        data = match.groupdict()
        try:
            timestamp = dt.datetime.strptime(data["time"], "%d/%b/%Y:%H:%M:%S %z").astimezone(TIMEZONE)
            status = int(data["status"])
            duration = float(data["duration"])
        except (ValueError, TypeError):
            invalid += 1
            continue
        earliest = timestamp if earliest is None or timestamp < earliest else earliest
        is_bot = bool(BOT_RE.search(data["agent"]))
        day = timestamp.date().isoformat()
        visitor = hashlib.sha256(
            f"{day}\0{data['ip']}\0{data['agent']}".encode("utf-8", "replace")
        ).hexdigest()[:24]
        for days, cutoff in cutoffs.items():
            if timestamp.date() < cutoff:
                continue
            bucket = buckets[days]
            daily = bucket["daily"][day]
            if is_bot:
                bucket["crawler_requests"] += 1
                daily["crawler_requests"] += 1
                continue
            bucket["visitors"].add(visitor)
            bucket["pageviews"] += 1
            bucket["statuses"][status] += 1
            bucket["durations"].append(duration)
            bucket["pages"][data["path"]] += 1
            bucket["sources"][source_name(data["referrer"], data["host"])] += 1
            country = data["country"].upper()
            bucket["countries"][country if re.fullmatch(r"[A-Z]{2}", country) else "未知"] += 1
            bucket["devices"][device(data["agent"])] += 1
            bucket["browsers"][browser(data["agent"])] += 1
            daily["visitors"].add(visitor)
            daily["pageviews"] += 1

    result = {
        "generated_at": now.isoformat(),
        "available_since": earliest.isoformat() if earliest else None,
        "timezone": "Asia/Shanghai",
        "invalid_lines": invalid,
        "ranges": {str(days): finalize(bucket) for days, bucket in buckets.items()},
        "definitions": {
            "visitors": "按日期、IP与浏览器组合估算；报告不保存或输出原始访客标识",
            "pageviews": "排除静态资源、普通API、健康检查和统计页的HTML页面请求",
            "crawler_requests": "按User-Agent启发式识别，仅供趋势判断",
        },
    }
    temporary = OUTPUT.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.chmod(0o640)
    temporary.replace(OUTPUT)


if __name__ == "__main__":
    main()
