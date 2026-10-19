"""对话记录按周压缩归档测试。"""

import sqlite3
import zipfile
from datetime import datetime, timedelta

from MyPythonUtility.FileSqliteHyridDB import HybridDB
from ServiceComponent.conversation_compaction import compact_conversation_records


def _make_records(root, specs):
    db = HybridDB(str(root))
    for content, days_ago in specs:
        index = db.add_text(content, category="analysis", name="analysis")
        if days_ago:
            conn = sqlite3.connect(root / "index.sqlite")
            conn.execute(
                "UPDATE records SET timestamp=? WHERE id=?",
                ((datetime.now() - timedelta(days=days_ago)).isoformat(), index))
            conn.commit()
            conn.close()


def test_compaction_archives_old_records_into_iso_week_zip(tmp_path):
    root = tmp_path / "conversation"
    # 两条记录都取上周周一（同一 ISO 周且必然超过 7 天保留期，任何星期几运行都成立）
    days_old = datetime.now().weekday() + 7
    _make_records(root, [("旧记录A", days_old), ("旧记录B", days_old), ("新记录", 1)])

    summary = compact_conversation_records(root, older_than_days=7)

    assert summary["archived"] == 2
    assert summary["skipped_recent"] == 1
    # 两条旧记录位于同一 ISO 周，应进入同一个 zip
    assert len(summary["archives"]) == 1
    archive = summary["archives"][0]
    assert "-W" in archive and archive.endswith(".zip")
    with zipfile.ZipFile(archive) as zf:
        contents = "".join(zf.read(n).decode("utf-8") for n in zf.namelist())
    assert "旧记录A" in contents and "旧记录B" in contents
    # 原散文件删除，新记录保留
    assert len(list((root / "analysis").glob("*.txt"))) == 1

    # 幂等：再次执行不重复归档
    again = compact_conversation_records(root, older_than_days=7)
    assert again["archived"] == 0
    conn = sqlite3.connect(root / "index.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM compaction").fetchone()[0] == 2
    conn.close()


def test_compaction_groups_records_by_their_own_week(tmp_path):
    root = tmp_path / "conversation"
    # 上周一与上上周一：必然分属不同 ISO 周且必然超过 7 天保留期
    this_week_monday_age = datetime.now().weekday() + 7
    _make_records(root, [("上上周", this_week_monday_age + 7), ("上周", this_week_monday_age)])
    summary = compact_conversation_records(root, older_than_days=7)
    # 两条记录分属不同 ISO 周 → 两个 zip
    assert summary["archived"] == 2
    assert len(summary["archives"]) == 2


def test_crash_between_zip_write_and_mark_does_not_duplicate(tmp_path):
    """模拟上次运行在 zip 写入后、标记前崩溃：重跑不得产生重复条目。"""
    root = tmp_path / "conversation"
    _make_records(root, [("崩溃恢复", 10)])
    # 找到待归档文件的 arcname，预先手工写入对应周的 zip（模拟崩溃现场）
    conn = sqlite3.connect(root / "index.sqlite")
    _id, rel = conn.execute("SELECT id, relative_path FROM records").fetchone()
    conn.close()
    from ServiceComponent.conversation_compaction import _iso_week_key
    from datetime import datetime as _dt
    record_time = _dt.now() - timedelta(days=10)
    archive_dir = root / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"analysis_{_iso_week_key(record_time)}.zip"
    arcname = rel.replace("\\", "/")
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(root / arcname, arcname)

    summary = compact_conversation_records(root, older_than_days=7)
    assert summary["archived"] == 1
    with zipfile.ZipFile(archive_path) as zf:
        assert zf.namelist() == [arcname]  # 无重复条目
    # 原文件删除、标记补齐
    assert not (root / arcname).exists()
    conn = sqlite3.connect(root / "index.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM compaction").fetchone()[0] == 1
    conn.close()
