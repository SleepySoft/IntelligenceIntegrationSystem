"""对话记录按周压缩归档。

策略：
- 热层：最近 7 天散文件保留在原目录，立即可查；
- 温层：超过保留期的记录按记录所在 ISO 周打包进
  ``<root>/archive/<category>_<YYYY>-W<ww>.zip``（每周一个压缩包），原文件删除；
- 任务每周运行一次，zip 以 append 模式写入：同一周内重复执行幂等，
  跨周补档的记录归入其自身所在周的压缩包；
- 索引（index.sqlite）全量保留，已归档记录在 ``compaction`` 表标注。

在线安全：只处理超过保留期的旧文件，不与正在写入的新记录冲突。
"""

from __future__ import annotations

import logging
import sqlite3
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_ROOT = "conversation"
DEFAULT_RETENTION_DAYS = 7


def _ensure_compaction_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS compaction ("
        " record_id INTEGER PRIMARY KEY,"
        " archive_path TEXT NOT NULL,"
        " compacted_at TEXT NOT NULL)"
    )


def _iso_week_key(value: datetime) -> str:
    iso = value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def compact_conversation_records(root: str | Path = DEFAULT_ROOT, *,
                                 older_than_days: int = DEFAULT_RETENTION_DAYS) -> dict:
    """把超过保留期的对话记录按周打包归档；返回本次处理摘要。"""
    root = Path(root)
    index_db = root / "index.sqlite"
    summary = {"archived": 0, "missing": 0, "archives": [], "skipped_recent": 0}
    if not index_db.exists():
        logger.warning("对话记录索引不存在: %s", index_db)
        return summary

    cutoff = datetime.now() - timedelta(days=older_than_days)
    conn = sqlite3.connect(index_db)
    try:
        _ensure_compaction_table(conn)
        rows = conn.execute(
            "SELECT r.id, r.timestamp, r.relative_path FROM records r "
            "LEFT JOIN compaction c ON c.record_id = r.id "
            "WHERE c.record_id IS NULL"
        ).fetchall()

        by_week: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for record_id, timestamp, relative_path in rows:
            try:
                record_time = datetime.fromisoformat(str(timestamp))
            except ValueError:
                continue
            if record_time >= cutoff:
                summary["skipped_recent"] += 1
                continue
            by_week[_iso_week_key(record_time)].append((record_id, relative_path))

        if not by_week:
            return summary

        archive_dir = root / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        for week, items in sorted(by_week.items()):
            category = Path(items[0][1]).parts[0] if items else "records"
            archive_path = archive_dir / f"{category}_{week}.zip"
            done_ids = []
            with zipfile.ZipFile(archive_path, "a", zipfile.ZIP_DEFLATED) as zf:
                existing = set(zf.namelist())
                for record_id, relative_path in items:
                    file_path = root / relative_path.replace("\\", "/")
                    if not file_path.exists():
                        summary["missing"] += 1
                        continue
                    arcname = relative_path.replace("\\", "/")
                    if arcname in existing:
                        arcname = f"{arcname}.{record_id}"
                    zf.write(file_path, arcname)
                    file_path.unlink()
                    done_ids.append(record_id)
            # zip 完整性校验通过后才写标注
            with zipfile.ZipFile(archive_path) as zf:
                if zf.testzip() is not None:
                    raise IOError(f"压缩包校验失败: {archive_path}")
            conn.executemany(
                "INSERT OR REPLACE INTO compaction (record_id, archive_path, compacted_at)"
                " VALUES (?, ?, ?)",
                [(rid, str(archive_path), datetime.now().isoformat()) for rid in done_ids],
            )
            conn.commit()
            summary["archived"] += len(done_ids)
            summary["archives"].append(str(archive_path))
            logger.info("对话记录归档: %s（%d 条）", archive_path, len(done_ids))
        return summary
    finally:
        conn.close()
