#!/usr/bin/env python3
"""按 Prompt 版本汇总 industry 灰度的价值率、总分和 RATE 分布。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from IntelligenceDomains.industry.schema import RATE_LEVELS
from ServiceComponent.intelligence_contracts import (
    APPENDIX_AI_MODEL,
    APPENDIX_FIELD,
    APPENDIX_PROMPT_VERSION,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TOTAL_SCORE,
    appendix_path,
)


RATE_DIMENSIONS = tuple(RATE_LEVELS)


def summarize_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        version = int(record.get("prompt_version") or 0)
        model = str(record.get("model") or "<unknown>")
        groups[(version, model)].append(record)

    rows: list[dict[str, Any]] = []
    for (version, model), items in sorted(groups.items()):
        valuable = [item for item in items if item.get("valuable")]
        scores = [float(item["total_score"]) for item in valuable
                  if item.get("total_score") is not None]
        rate_means = {}
        for dimension in RATE_DIMENSIONS:
            values = [
                float(item.get("rate", {}).get(dimension))
                for item in valuable
                if item.get("rate", {}).get(dimension) is not None
            ]
            rate_means[dimension] = round(mean(values), 2) if values else None
        rows.append({
            "prompt_version": version,
            "model": model,
            "completed": len(items),
            "valuable": len(valuable),
            "low_value": len(items) - len(valuable),
            "valuable_rate": round(len(valuable) / len(items), 4),
            "mean_total_score": round(mean(scores), 2) if scores else None,
            "rate_means": rate_means,
        })
    return rows


def _records(collection, *, valuable: bool, query: dict[str, Any]):
    projection = {
        "_id": 0,
        appendix_path(APPENDIX_PROMPT_VERSION): 1,
        appendix_path(APPENDIX_AI_MODEL): 1,
        appendix_path(APPENDIX_TOTAL_SCORE): 1,
        "RATE": 1,
    }
    for document in collection.find(query, projection):
        appendix = document.get(APPENDIX_FIELD) or {}
        yield {
            "prompt_version": appendix.get(APPENDIX_PROMPT_VERSION),
            "model": appendix.get(APPENDIX_AI_MODEL),
            "total_score": appendix.get(APPENDIX_TOTAL_SCORE),
            "rate": document.get("RATE") or {},
            "valuable": valuable,
        }


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Prompt | 模型 | 完成 | 有价值 | 低价值 | 有价值率 | 平均总分 |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        score = row["mean_total_score"]
        lines.append(
            f"| v{row['prompt_version']} | {row['model']} | {row['completed']} | "
            f"{row['valuable']} | {row['low_value']} | {row['valuable_rate']:.1%} | "
            f"{score if score is not None else '-'} |"
        )
    lines.extend(["", "RATE 均值（仅有价值结果）：", ""])
    lines.append("| Prompt | 模型 | " + " | ".join(RATE_DIMENSIONS) + " |")
    lines.append("|---:|---|" + "---:|" * len(RATE_DIMENSIONS))
    for row in rows:
        values = [
            "-" if row["rate_means"][name] is None
            else str(row["rate_means"][name])
            for name in RATE_DIMENSIONS
        ]
        lines.append(
            f"| v{row['prompt_version']} | {row['model']} | "
            + " | ".join(values) + " |"
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=27017)
    parser.add_argument("--database", default="IntelligenceIntegrationSystem")
    parser.add_argument("--prefix", default="industry_intelligence_")
    parser.add_argument("--versions", default="1,2", help="逗号分隔的 Prompt 版本")
    parser.add_argument("--since", help="只统计该 ISO 时间之后归档的结果")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是 Markdown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    versions = [int(item.strip()) for item in args.versions.split(",") if item.strip()]
    query: dict[str, Any] = {
        appendix_path(APPENDIX_PROMPT_VERSION): {"$in": versions},
    }
    if args.since:
        query[appendix_path(APPENDIX_TIME_ARCHIVED)] = {
            "$gte": datetime.fromisoformat(args.since),
        }

    client = MongoClient(args.host, args.port, serverSelectionTimeoutMS=5000)
    try:
        database = client[args.database]
        records = [
            *_records(database[f"{args.prefix}v4_archived"], valuable=True, query=query),
            *_records(database[f"{args.prefix}v4_low_value"], valuable=False, query=query),
        ]
        rows = summarize_records(records)
    finally:
        client.close()

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
