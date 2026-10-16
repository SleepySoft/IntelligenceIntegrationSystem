# EntityAlias/index.py
# 内存索引：精确匹配 + 部分/模糊匹配（纯 Python，无外部依赖）。

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Tuple

from EntityAlias.model import Entity
from EntityAlias.normalize import normalize_name


def _bigrams(s: str) -> set:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def fuzzy_score(query: str, candidate: str) -> float:
    """归一化后的相似度评分（0~1）：精确 > 包含 > 编辑距离/bigram 混合。"""
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    # 部分匹配：一方包含另一方，按长度比给分（保底 0.7）
    if query in candidate or candidate in query:
        ratio = min(len(query), len(candidate)) / max(len(query), len(candidate))
        return 0.7 + 0.3 * ratio
    ratio = difflib.SequenceMatcher(None, query, candidate).ratio()
    qb, cb = _bigrams(query), _bigrams(candidate)
    jaccard = len(qb & cb) / len(qb | cb) if (qb or cb) else 0.0
    return max(ratio, jaccard)


class AliasIndex:
    """归一化名 -> 实体 的精确索引 + 全量名称列表（供模糊扫描）。"""

    def __init__(self):
        # norm_key -> (entity_id, 原始名, lang, is_canonical)
        self._exact: Dict[str, Tuple[str, str, str, bool]] = {}

    # ------------------------------------------------------------------ 精确

    def resolve(self, name: str) -> Optional[Tuple[str, str, str, bool]]:
        return self._exact.get(normalize_name(name))

    def name_owner(self, name: str) -> Optional[str]:
        hit = self._exact.get(normalize_name(name))
        return hit[0] if hit else None

    # ------------------------------------------------------------------ 模糊

    def fuzzy_lookup(self, name: str, *, limit: int = 5,
                     min_score: float = 0.4) -> List[Dict[str, Any]]:
        key = normalize_name(name)
        if not key:
            return []
        scored: List[Tuple[float, str, str, str, bool]] = []
        for cand_key, (entity_id, raw, lang, is_canonical) in self._exact.items():
            score = fuzzy_score(key, cand_key)
            if score >= min_score:
                scored.append((score, entity_id, raw, lang, is_canonical))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "entity_id": entity_id,
                "matched_name": raw,
                "lang": lang,
                "is_canonical": is_canonical,
                "score": round(score, 4),
                "source": "partial" if score >= 0.7 else "fuzzy",
            }
            for score, entity_id, raw, lang, is_canonical in scored[:limit]
        ]

    # ------------------------------------------------------------------ 维护

    def rebuild(self, entities: List[Entity]) -> None:
        self._exact.clear()
        for entity in entities:
            self.add_entity(entity)

    def add_entity(self, entity: Entity) -> None:
        self.remove_entity(entity.id)
        for entry in entity.all_names():
            key = normalize_name(entry.name)
            if key:
                self._exact[key] = (entity.id, entry.name, entry.lang,
                                    entry.name == entity.canonical)

    def remove_entity(self, entity_id: str) -> None:
        stale = [k for k, v in self._exact.items() if v[0] == entity_id]
        for k in stale:
            del self._exact[k]

    @property
    def name_count(self) -> int:
        return len(self._exact)
