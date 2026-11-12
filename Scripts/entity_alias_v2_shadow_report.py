#!/usr/bin/env python3
"""只读查看 EntityAlias V2 shadow 进度与弃权分布。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from GlobalConfig import DATA_PATH


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(
        f"file:{path.resolve()}?mode=ro", uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    return connection


def build_report(root: Path, *, example_limit: int = 10) -> dict:
    fact_path = root / "entities.db"
    shadow_path = root / "shadow.db"
    with _connect_readonly(fact_path) as facts:
        entity_count = facts.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        name_count = facts.execute("SELECT COUNT(*) FROM entity_names").fetchone()[0]
        type_rows = facts.execute(
            """SELECT canonical_type, status, COUNT(*) AS count
               FROM entities GROUP BY canonical_type, status
               ORDER BY count DESC, canonical_type""").fetchall()
    with _connect_readonly(shadow_path) as shadow:
        observation_count = shadow.execute(
            "SELECT COUNT(*) FROM observations").fetchone()[0]
        resolution_rows = shadow.execute(
            """SELECT v2_status, COUNT(*) AS count FROM observations
               GROUP BY v2_status ORDER BY count DESC""").fetchall()
        enrichment_rows = shadow.execute(
            """SELECT status, COUNT(*) AS count, SUM(attempts) AS attempts
               FROM enrichment_ledger GROUP BY status ORDER BY count DESC""").fetchall()
        examples = shadow.execute(
            """SELECT surface, mention_type, canonical_type, v2_status, reason,
                      v2_candidate_ids
               FROM observations
               WHERE v2_status IN ('ambiguous', 'type_conflict',
                                   'context_required', 'unverified')
               ORDER BY updated_at DESC LIMIT ?""",
            (max(0, example_limit),),
        ).fetchall()
    return {
        "root": str(root.resolve()),
        "facts": {
            "entities": int(entity_count),
            "names": int(name_count),
            "by_type_status": [dict(row) for row in type_rows],
        },
        "shadow": {
            "observations": int(observation_count),
            "resolution_statuses": {
                str(row["v2_status"]): int(row["count"])
                for row in resolution_rows
            },
            "enrichment_statuses": {
                str(row["status"]): {
                    "typed_names": int(row["count"]),
                    "attempts": int(row["attempts"] or 0),
                }
                for row in enrichment_rows
            },
            "examples": [dict(row) for row in examples],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", default=str(Path(DATA_PATH) / "entity_alias_v2"),
        help="包含 entities.db 和 shadow.db 的目录",
    )
    parser.add_argument("--examples", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(
        build_report(Path(args.root), example_limit=args.examples),
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
