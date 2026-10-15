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
    # 取上周周一和周三（保证同一 ISO 周且都超过 7 天保留期）
    last_monday = datetime.now() - timedelta(days=datetime.now().weekday() + 7)
    days_a = (datetime.now() - last_monday).days
    days_b = days_a - 2
    _make_records(root, [("旧记录A", days_a), ("旧记录B", days_b), ("新记录", 1)])

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
    _make_records(root, [("上上周", 13), ("上周", 9)])
    summary = compact_conversation_records(root, older_than_days=7)
    # 两条记录分属不同 ISO 周 → 两个 zip
    assert summary["archived"] == 2
    assert len(summary["archives"]) == 2
