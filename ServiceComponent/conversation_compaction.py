"""对话记录按周压缩归档。

策略：
- 热层：最近 7 天散文件保留在原目录，立即可查；
- 温层：超过保留期的记录按记录所在 ISO 周打包进
  ``<root>/archive/<category>_<YYYY>-W<ww>.zip``（每周一个压缩包），原文件删除；
- 任务每周运行一次，zip 以 append 模式写入：同一周内重复执行幂等，
  跨周补档的记录归入其自身所在周的压缩包；
- 索引（index.sqlite）全量保留，已归档记录在 ``compaction`` 表标注。

在线安全：只处理超过保留期的旧文件，不与正在写入的新记录冲突。

幂等保证：
- 已归档记录在 ``compaction`` 表中标注，重跑直接跳过；
- zip 写入与标记之间崩溃时，重跑发现 arcname 已存在于 zip，则跳过写入、
  仅补删除与标记，不产生重复条目；
- zip 关闭并通过完整性校验后才删除原文件、写标记；
- 跨进程锁文件（archive/.compact.lock）防止调度任务与手动脚本并发执行。
"""

from __future__ import annotations

import logging
import os
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
        lock_path = archive_dir / ".compact.lock"
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            logger.warning("另一归档进程正在运行，本次跳过")
            summary["locked"] = True
            return summary
        try:
            for week, items in sorted(by_week.items()):
                category = Path(items[0][1]).parts[0] if items else "records"
                archive_path = archive_dir / f"{category}_{week}.zip"
                pending: list[tuple[int, Path, str]] = []
                for record_id, relative_path in items:
                    file_path = root / relative_path.replace("\\", "/")
                    if not file_path.exists():
                        summary["missing"] += 1
                        continue
                    pending.append((record_id, file_path, relative_path.replace("\\", "/")))
                if not pending:
                    continue
                # 1) 写入 zip（已存在的 arcname 说明上次崩溃前已写入，跳过不再重复）
                with zipfile.ZipFile(archive_path, "a", zipfile.ZIP_DEFLATED) as zf:
                    existing = set(zf.namelist())
                    for record_id, file_path, arcname in pending:
                        if arcname not in existing:
                            zf.write(file_path, arcname)
                            existing.add(arcname)
                # 2) 关包后先校验完整性，再删原文件、写标记
                with zipfile.ZipFile(archive_path) as zf:
                    if zf.testzip() is not None:
                        raise IOError(f"压缩包校验失败: {archive_path}")
                done_ids = []
                for record_id, file_path, _arcname in pending:
                    file_path.unlink()
                    done_ids.append(record_id)
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
            os.close(lock_fd)
            lock_path.unlink(missing_ok=True)
    finally:
        conn.close()
