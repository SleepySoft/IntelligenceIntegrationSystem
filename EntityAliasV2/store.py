"""EntityAlias V2 的 SQLite 事实存储。

数据库是 V2 的唯一事实源；不读取或生成 V1 JSONL。名称表不保存实体类型，类型只存在于
实体表中，类型化查询键通过 JOIN 派生，避免同一别名组内部发生类型漂移。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from .model import AliasV2Error, EntityName, EntityRecord


SCHEMA_VERSION = 1


class SQLiteAliasV2Store:
    """线程安全的 V2 SQLite Store。"""

    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._create_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                canonical_type TEXT NOT NULL,
                status TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                native_lang TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entity_names (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                lang TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT 'direct',
                created_at TEXT NOT NULL,
                UNIQUE(entity_id, normalized_name)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS one_canonical_name_per_entity
                ON entity_names(entity_id) WHERE kind = 'canonical';
            CREATE UNIQUE INDEX IF NOT EXISTS one_localized_name_per_language
                ON entity_names(entity_id, lang) WHERE kind = 'localized';
            CREATE INDEX IF NOT EXISTS entity_names_normalized
                ON entity_names(normalized_name);
            CREATE INDEX IF NOT EXISTS entities_type_status
                ON entities(canonical_type, status);

            CREATE TABLE IF NOT EXISTS entity_refs (
                entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                namespace TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(entity_id, namespace),
                UNIQUE(namespace, value)
            );
            """
        )
        current = self._connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if current is None:
            self._connection.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif int(current["value"]) != SCHEMA_VERSION:
            raise AliasV2Error(
                f"不支持的 EntityAliasV2 schema：{current['value']}",
                "schema_version",
            )

    def transaction(self):
        return _Transaction(self)

    def insert_entity(
        self,
        *,
        entity_id: str,
        canonical_type: str,
        status: str,
        description: str,
        native_lang: str,
        created_at: str,
        updated_at: str,
        names: Iterable[EntityName],
        refs: dict[str, str],
    ) -> EntityRecord:
        with self.transaction():
            self._connection.execute(
                """INSERT INTO entities(
                       id, canonical_type, status, description, native_lang,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    entity_id, canonical_type, status, description, native_lang,
                    created_at, updated_at,
                ),
            )
            self._insert_names(entity_id, names, created_at)
            self._replace_refs(entity_id, refs)
        value = self.get_entity(entity_id)
        if value is None:  # pragma: no cover - INSERT 成功后的防御分支
            raise AliasV2Error("实体写入后无法读取", "storage_error")
        return value

    def _insert_names(
        self, entity_id: str, names: Iterable[EntityName], created_at: str,
    ) -> None:
        self._connection.executemany(
            """INSERT INTO entity_names(
                   entity_id, name, normalized_name, lang, kind, role,
                   resolution, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    entity_id, item.name, item.normalized_name, item.lang,
                    item.kind, item.role, item.resolution, created_at,
                )
                for item in names
            ],
        )

    def _replace_refs(self, entity_id: str, refs: dict[str, str]) -> None:
        self._connection.execute(
            "DELETE FROM entity_refs WHERE entity_id = ?", (entity_id,))
        self._connection.executemany(
            "INSERT INTO entity_refs(entity_id, namespace, value) VALUES (?, ?, ?)",
            [(entity_id, key, value) for key, value in refs.items()],
        )

    def replace_entity(
        self,
        entity_id: str,
        *,
        canonical_type: str,
        status: str,
        description: str,
        native_lang: str,
        updated_at: str,
        names: Iterable[EntityName],
        refs: dict[str, str],
    ) -> EntityRecord:
        with self.transaction():
            cursor = self._connection.execute(
                """UPDATE entities
                   SET canonical_type = ?, status = ?, description = ?,
                       native_lang = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    canonical_type, status, description, native_lang,
                    updated_at, entity_id,
                ),
            )
            if cursor.rowcount != 1:
                raise AliasV2Error(f"实体不存在：{entity_id}", "not_found")
            self._connection.execute(
                "DELETE FROM entity_names WHERE entity_id = ?", (entity_id,))
            self._insert_names(entity_id, names, updated_at)
            self._replace_refs(entity_id, refs)
        value = self.get_entity(entity_id)
        if value is None:  # pragma: no cover
            raise AliasV2Error("实体更新后无法读取", "storage_error")
        return value

    def delete_entity(self, entity_id: str) -> bool:
        with self.transaction():
            cursor = self._connection.execute(
                "DELETE FROM entities WHERE id = ?", (entity_id,))
        return cursor.rowcount == 1

    def get_entity(self, entity_id: str) -> EntityRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
            return self._record(row) if row is not None else None

    def find_by_ref(self, namespace: str, value: str) -> EntityRecord | None:
        """按外部稳定标识查实体，例如 ``wikidata/Q312``。"""

        with self._lock:
            row = self._connection.execute(
                """SELECT e.* FROM entity_refs AS r
                   JOIN entities AS e ON e.id = r.entity_id
                   WHERE r.namespace = ? AND r.value = ?""",
                (str(namespace).strip().casefold(), str(value).strip()),
            ).fetchone()
            return self._record(row) if row is not None else None

    def list_entities(
        self, *, canonical_type: str | None = None, limit: int = 100,
    ) -> list[EntityRecord]:
        with self._lock:
            if canonical_type:
                rows = self._connection.execute(
                    """SELECT * FROM entities WHERE canonical_type = ?
                       ORDER BY updated_at DESC LIMIT ?""",
                    (canonical_type, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM entities ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._record(row) for row in rows]

    def find_by_name(
        self, normalized_name: str, *, canonical_type: str | None = None,
    ) -> list[tuple[EntityRecord, EntityName]]:
        """按名称及可选 canonical type 召回；允许返回多个同名实体。"""

        sql = """
            SELECT n.id AS matched_id, n.name AS matched_name,
                   n.normalized_name AS matched_normalized_name,
                   n.lang AS matched_lang, n.kind AS matched_kind,
                   n.role AS matched_role, n.resolution AS matched_resolution,
                   e.*
            FROM entity_names AS n
            JOIN entities AS e ON e.id = n.entity_id
            WHERE n.normalized_name = ?
        """
        parameters: list[object] = [normalized_name]
        if canonical_type is not None:
            sql += " AND e.canonical_type = ?"
            parameters.append(canonical_type)
        sql += " ORDER BY e.status DESC, e.id"
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
            result = []
            for row in rows:
                entity = self._record(row)
                matched = EntityName(
                    name=row["matched_name"],
                    normalized_name=row["matched_normalized_name"],
                    lang=row["matched_lang"],
                    kind=row["matched_kind"],
                    role=row["matched_role"],
                    resolution=row["matched_resolution"],
                )
                result.append((entity, matched))
            return result

    def count_entities(self) -> int:
        with self._lock:
            return int(self._connection.execute(
                "SELECT COUNT(*) FROM entities").fetchone()[0])

    def count_names(self) -> int:
        with self._lock:
            return int(self._connection.execute(
                "SELECT COUNT(*) FROM entity_names").fetchone()[0])

    def _record(self, row: sqlite3.Row) -> EntityRecord:
        entity_id = str(row["id"])
        names = self._connection.execute(
            """SELECT name, normalized_name, lang, kind, role, resolution
               FROM entity_names WHERE entity_id = ?
               ORDER BY CASE kind
                   WHEN 'canonical' THEN 0 WHEN 'localized' THEN 1 ELSE 2 END,
                   lang, name""",
            (entity_id,),
        ).fetchall()
        refs = self._connection.execute(
            "SELECT namespace, value FROM entity_refs WHERE entity_id = ?",
            (entity_id,),
        ).fetchall()
        return EntityRecord(
            id=entity_id,
            canonical_type=str(row["canonical_type"]),
            status=str(row["status"]),
            description=str(row["description"]),
            native_lang=str(row["native_lang"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            names=tuple(EntityName(
                name=str(item["name"]),
                normalized_name=str(item["normalized_name"]),
                lang=str(item["lang"]),
                kind=str(item["kind"]),
                role=str(item["role"]),
                resolution=str(item["resolution"]),
            ) for item in names),
            refs={str(item["namespace"]): str(item["value"]) for item in refs},
        )


class _Transaction:
    def __init__(self, store: SQLiteAliasV2Store):
        self.store = store

    def __enter__(self):
        self.store._lock.acquire()
        self.store._connection.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.store._connection.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.store._lock.release()
        return False
