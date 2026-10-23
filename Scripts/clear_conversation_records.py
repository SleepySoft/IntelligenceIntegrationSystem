"""废弃历史对话记录：删除记录文件与索引行。

用途：对话记录长期累积且格式已换代时清空存量。新格式记录从清空后开始积累。

用法：
    python Scripts/clear_conversation_records.py --dry-run          # 预览（默认）
    python Scripts/clear_conversation_records.py --yes              # 删除全部记录
    python Scripts/clear_conversation_records.py --before 2026-10-15 --yes
    python Scripts/clear_conversation_records.py --category analysis --yes
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_ROOT = "conversation"


def clear_conversation_records(root: str | Path = DEFAULT_ROOT, *,
                               before: datetime | None = None,
                               category: str | None = None) -> dict:
    """删除匹配条件的记录文件与索引行，返回统计。"""
    root = Path(root)
    index_db = root / "index.sqlite"
    summary = {"files_deleted": 0, "index_deleted": 0, "missing_files": 0}
    if not index_db.exists():
        print(f"索引不存在: {index_db}")
        return summary

    clauses, params = [], []
    if before is not None:
        clauses.append("timestamp < ?")
        params.append(before.isoformat())
    if category:
        clauses.append("category = ?")
        params.append(category)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = sqlite3.connect(index_db)
    try:
        rows = conn.execute(
            f"SELECT id, relative_path FROM records{where}", params).fetchall()
        for record_id, relative_path in rows:
            file_path = root / str(relative_path).replace("\\", "/")
            try:
                file_path.unlink()
                summary["files_deleted"] += 1
            except FileNotFoundError:
                summary["missing_files"] += 1
        conn.execute(f"DELETE FROM records{where}", params)
        conn.commit()
        summary["index_deleted"] = len(rows)
        conn.execute("VACUUM")
    finally:
        conn.close()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--before", default="", help="只删除早于该日期的记录（YYYY-MM-DD）")
    parser.add_argument("--category", default="", help="只删除该类别（如 analysis）")
    parser.add_argument("--yes", action="store_true", help="确认执行删除")
    parser.add_argument("--dry-run", action="store_true", help="只统计不删除（默认）")
    args = parser.parse_args()

    before = datetime.fromisoformat(args.before) if args.before else None
    if not args.yes:
        # dry-run：只统计数量
        index_db = Path(args.root) / "index.sqlite"
        conn = sqlite3.connect(index_db)
        clauses, params = [], []
        if before:
            clauses.append("timestamp < ?")
            params.append(before.isoformat())
        if args.category:
            clauses.append("category = ?")
            params.append(args.category)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        count = conn.execute(f"SELECT COUNT(*) FROM records{where}", params).fetchone()[0]
        conn.close()
        print(f"[dry-run] 将删除 {count} 条记录（文件+索引）。加 --yes 实际执行。")
        return

    summary = clear_conversation_records(
        args.root, before=before, category=args.category or None)
    print(f"已删除文件 {summary['files_deleted']} 个"
          f"（缺失 {summary['missing_files']}），索引行 {summary['index_deleted']} 条。")


if __name__ == "__main__":
    main()
