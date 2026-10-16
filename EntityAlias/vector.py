# EntityAlias/vector.py
# 可选向量化：找不到精确匹配时，用语义相似度查找最近的名字。
#
# 索引实现通过 duck typing 注入（upsert_name/remove_name/query），
# 默认空实现（向量能力关闭）。IIS 集成时可用 VectorDB 服务的独立集合承载，
# 模块本身不 import VectorDB。

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List

from EntityAlias.normalize import normalize_name

logger = logging.getLogger(__name__)


class NullNearestIndex:
    """向量能力关闭时的空实现。"""

    available = False

    def upsert_name(self, name: str) -> None:
        pass

    def remove_name(self, name: str) -> None:
        pass

    def rebuild(self, names: List[str]) -> None:
        pass

    def query(self, text: str, top_n: int = 5) -> List[Dict[str, Any]]:
        return []


class VectorDBNearestIndex:
    """基于 VectorDB 服务独立集合的最近名索引。

    collection 需具备 upsert(doc_id, text, metadata) / search(query, top_n) /
    delete(doc_id) 接口（VectorDB.VectorDBClient.RemoteCollection 即满足）。
    分块参数对此场景无意义，建议 chunk_size 足够大以保证一个名字一个 chunk。
    """

    available = True

    def __init__(self, collection):
        self.collection = collection

    @staticmethod
    def _doc_id(name: str) -> str:
        return "name_" + hashlib.sha1(normalize_name(name).encode("utf-8")).hexdigest()

    def upsert_name(self, name: str) -> None:
        try:
            self.collection.upsert(
                doc_id=self._doc_id(name), text=name, metadata={"name": name})
        except Exception:
            logger.warning("向量索引写入失败：%r", name, exc_info=True)

    def remove_name(self, name: str) -> None:
        try:
            self.collection.delete(self._doc_id(name))
        except Exception:
            logger.warning("向量索引删除失败：%r", name, exc_info=True)

    def rebuild(self, names: List[str]) -> None:
        # upsert 语义幂等（doc_id 由名归一化哈希决定），全量覆盖即可。
        for name in names:
            self.upsert_name(name)

    def query(self, text: str, top_n: int = 5) -> List[Dict[str, Any]]:
        try:
            records = self.collection.search(query=text, top_n=top_n) or []
        except Exception:
            logger.warning("向量索引查询失败：%r", text, exc_info=True)
            return []
        results = []
        for record in records:
            metadata = record.get("metadata") or {}
            matched = metadata.get("name") or record.get("doc_id")
            if matched:
                results.append({"matched_name": matched,
                                "score": round(float(record.get("score", 0.0)), 4),
                                "source": "vector"})
        return results
