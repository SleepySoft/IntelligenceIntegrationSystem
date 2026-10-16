# EntityAlias/store.py
# JSONL 文本存储：事实源，git 友好。
#
# 设计：
# - 每个实体类型一个文件：<data_dir>/<type>.jsonl，一行一个实体 JSON；
# - 文件按标准名排序写入，保证 git diff 最小、可读；
# - 写操作 = 内存更新 + 整文件原子重写（tmp + replace），数据量为千级时开销可忽略；
# - 启动时 load_all() 全量加载，运行期以内存为准。

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Dict, Iterable, List, Optional

from EntityAlias.model import Entity

logger = logging.getLogger(__name__)

_SAFE_TYPE_RE = re.compile(r"[^a-z0-9_]+")


def _type_filename(entity_type: str) -> str:
    safe = _SAFE_TYPE_RE.sub("_", (entity_type or "other").lower()).strip("_")
    return f"{safe or 'other'}.jsonl"


class JSONLStore:
    """按实体类型分文件的 JSONL 存储（线程安全）。"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._entities: Dict[str, Entity] = {}
        self.reload()

    # ------------------------------------------------------------------ 加载

    def reload(self) -> None:
        with self._lock:
            entities: Dict[str, Entity] = {}
            for filename in os.listdir(self.data_dir):
                if not filename.endswith(".jsonl"):
                    continue
                path = os.path.join(self.data_dir, filename)
                with open(path, "r", encoding="utf-8") as f:
                    for lineno, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entity = Entity.from_dict(json.loads(line))
                        except Exception as exc:
                            logger.warning("跳过损坏行 %s:%d: %s", filename, lineno, exc)
                            continue
                        entities[entity.id] = entity
            self._entities = entities

    # ------------------------------------------------------------------ 查询

    def all(self) -> List[Entity]:
        with self._lock:
            return list(self._entities.values())

    def get(self, entity_id: str) -> Optional[Entity]:
        with self._lock:
            return self._entities.get(entity_id)

    def exists(self, entity_id: str) -> bool:
        with self._lock:
            return entity_id in self._entities

    def count(self) -> int:
        with self._lock:
            return len(self._entities)

    # ------------------------------------------------------------------ 写入

    def put(self, entity: Entity) -> None:
        """新增或更新实体，并重写其类型文件。"""
        with self._lock:
            old = self._entities.get(entity.id)
            old_type = old.type if old else None
            self._entities[entity.id] = entity
            self._write_type_file(entity.type)
            if old_type and old_type != entity.type:
                self._write_type_file(old_type)

    def delete(self, entity_id: str) -> bool:
        with self._lock:
            old = self._entities.pop(entity_id, None)
            if old is None:
                return False
            self._write_type_file(old.type)
            return True

    # ------------------------------------------------------------------ 持久化

    def _write_type_file(self, entity_type: str) -> None:
        """原子重写某类型的 JSONL 文件（按标准名排序，保证 diff 稳定）。"""
        entities = sorted(
            (e for e in self._entities.values() if e.type == entity_type),
            key=lambda e: e.canonical,
        )
        path = os.path.join(self.data_dir, _type_filename(entity_type))
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            for e in entities:
                f.write(json.dumps(e.to_dict(), ensure_ascii=False, sort_keys=True))
                f.write("\n")
        os.replace(tmp_path, path)

    def files(self) -> Iterable[str]:
        with self._lock:
            return sorted(
                f for f in os.listdir(self.data_dir) if f.endswith(".jsonl"))
