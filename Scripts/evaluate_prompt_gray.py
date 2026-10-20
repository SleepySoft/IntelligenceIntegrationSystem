"""按干净时间窗评估两个生产 Prompt 版本，不混入 shadow 调用。

示例：
    python Scripts/evaluate_prompt_gray.py --since 2026-10-20T22:15:00+08:00
    python Scripts/evaluate_prompt_gray.py --control 48 --candidate 50 --hours 12
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymongo import MongoClient


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]


def _rate(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.1%}" if denominator else "-"


def _parse_since(value: str | None, hours: int) -> str:
    if not value:
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("--since 必须包含时区，例如 +08:00")
    return parsed.astimezone(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=int, default=48)
    parser.add_argument("--candidate", type=int, default=50)
    parser.add_argument("--since", help="ISO 8601 起始时间，必须含时区")
    parser.add_argument("--hours", type=int, default=24, help="未给 --since 时的回看小时数")
    parser.add_argument("--stats-db", default="_data/analysis_stats.db")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    parser.add_argument("--mongo-db", default="IntelligenceIntegrationSystem")
    args = parser.parse_args()

    since = _parse_since(args.since, args.hours)
    versions = (args.control, args.candidate)
    conn = sqlite3.connect(Path(args.stats_db))
    conn.row_factory = sqlite3.Row
    outcomes = conn.execute(
        "SELECT * FROM outcomes WHERE shadow=0 AND prompt_version IN (?,?) AND ts>=?",
        (*versions, since),
    ).fetchall()
    if not outcomes:
        print(f"{since} 以后没有 v{args.control}/v{args.candidate} 主链统计。")
        return

    mongo = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=3000)[args.mongo_db]
    print(f"评估时间窗（UTC）: {since} -> {datetime.now(timezone.utc).isoformat()}")
    print("只统计 shadow=0；不同模型必须分层解读。\n")

    models = sorted({str(row["ai_model"] or "") for row in outcomes})
    for model in models:
        print(f"=== {model or 'unknown model'} ===")
        for version in versions:
            rows = [row for row in outcomes
                    if row["prompt_version"] == version and str(row["ai_model"] or "") == model]
            if not rows:
                print(f"v{version}: n=0")
                continue
            uuids = [row["uuid"] for row in rows]
            marks = ",".join("?" for _ in uuids)
            attempts = conn.execute(
                f"SELECT * FROM attempts WHERE shadow=0 AND prompt_version=? "
                f"AND ai_model=? AND uuid IN ({marks})",
                (version, model, *uuids),
            ).fetchall()
            latency = [int(row["latency_ms"]) for row in attempts
                       if row["latency_ms"] is not None]
            successes = sum(row["outcome"] == "success" for row in rows)
            one_attempt = sum(int(row["attempts_used"] or 0) == 1 for row in rows)
            errors = Counter(row["error_class"] for row in attempts if row["error_class"])

            valuable = list(mongo.intelligence_v4_archived.find(
                {"UUID": {"$in": uuids}},
                {"UUID": 1, "EVENT_EXTRACTION.events": 1, "APPENDIX": 1},
            ))
            low = list(mongo.intelligence_v4_low_value.find(
                {"UUID": {"$in": uuids}},
                {"UUID": 1, "NON_INTELLIGENCE_REASON": 1},
            ))
            qualities = [doc.get("APPENDIX", {}).get("__EXTRACTION_QUALITY__", {})
                         for doc in valuable]
            quality_docs = sum(
                bool(q.get("unregistered_role_count") or q.get("role_type_conflict_count"))
                for q in qualities
            )
            unregistered = sum(q.get("unregistered_role_count", 0) for q in qualities)
            conflicts = sum(q.get("role_type_conflict_count", 0) for q in qualities)

            def grounding(kind: str) -> str:
                hit = sum(q.get("surface_grounding", {}).get(kind, {}).get("hit", 0)
                          for q in qualities)
                total = sum(q.get("surface_grounding", {}).get(kind, {}).get("total", 0)
                            for q in qualities)
                return f"{hit}/{total} ({_rate(hit, total)})"

            scores = [doc.get("APPENDIX", {}).get("__TOTAL_SCORE__") for doc in valuable]
            scores = [float(score) for score in scores if score is not None]
            reasons = Counter(doc.get("NON_INTELLIGENCE_REASON") for doc in low)
            print(
                f"v{version}: n={len(rows)} success={_rate(successes, len(rows))} "
                f"one_attempt={_rate(one_attempt, len(rows))} "
                f"calls/item={len(attempts) / len(rows):.2f} "
                f"p50={_percentile(latency, .50)}ms p95={_percentile(latency, .95)}ms "
                f"errors={dict(errors) or '-'}"
            )
            print(
                f"  valuable={len(valuable)} low={len(low)} pending={len(rows)-len(valuable)-len(low)} "
                f"avg_score={(sum(scores)/len(scores)) if scores else 0:.2f} "
                f"quality_docs={quality_docs}/{len(valuable)} "
                f"unregistered={unregistered} conflicts={conflicts} "
                f"predicate_grounding={grounding('predicate')} "
                f"time_grounding={grounding('time')}"
            )
            print(f"  low_reasons={dict(reasons) or '-'}")
        print()


if __name__ == "__main__":
    main()
