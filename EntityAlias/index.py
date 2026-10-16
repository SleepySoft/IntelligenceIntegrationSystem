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
    """归一化名 -> 实体 的精确索引 + 模糊扫描预过滤。

    - ``_exact``：norm_key -> (entity_id, 原始名, lang, is_canonical)
    - ``_by_entity``：entity_id -> set(norm_key)，保证 remove 为 O(名字数)
    - ``_bigram_postings``：bigram -> set(norm_key)，名字数超过阈值后模糊查询
      先按 bigram 重叠度预过滤，再对小候选集跑 SequenceMatcher
    """

    _PREFILTER_MIN_NAMES = 20000  # 低于此规模全表扫描已足够快

    def __init__(self):
        self._exact: Dict[str, Tuple[str, str, str, bool]] = {}
        self._by_entity: Dict[str, set] = {}
        self._bigram_postings: Dict[str, set] = {}
        self._postings_on = False

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
        candidate_keys = self._prefilter_keys(key)
        scored: List[Tuple[float, str, str, str, bool]] = []
        for cand_key in candidate_keys:
            entity_id, raw, lang, is_canonical = self._exact[cand_key]
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

    def _prefilter_keys(self, key: str):
        """返回候选 norm_key 集合。小规模全扫；大规模用 bigram 倒排预过滤。"""
        if len(self._exact) < self._PREFILTER_MIN_NAMES:
            return list(self._exact.keys())
        q_bigrams = _bigrams(key)
        if not q_bigrams:
            return [key] if key in self._exact else []
        overlap: Dict[str, int] = {}
        for bigram in q_bigrams:
            for cand_key in self._bigram_postings.get(bigram, ()):
                overlap[cand_key] = overlap.get(cand_key, 0) + 1
        # 阈值：至少覆盖查询 bigram 的 1/3；包含关系（部分匹配）恒入选
        threshold = max(1, len(q_bigrams) // 3)
        candidates = [k for k, cnt in overlap.items() if cnt >= threshold]
        candidates.extend(k for k in self._exact if (key in k or k in key))
        if key in self._exact:
            candidates.append(key)
        return list(set(candidates))

    # ------------------------------------------------------------------ 维护

    def rebuild(self, entities: List[Entity]) -> None:
        self._exact.clear()
        self._by_entity.clear()
        self._bigram_postings.clear()
        self._postings_on = False
        for entity in entities:
            self._add_entity_names(entity)
        self._maybe_enable_postings()

    def add_entity(self, entity: Entity) -> None:
        self.remove_entity(entity.id)
        self._add_entity_names(entity)
        self._maybe_enable_postings()

    def _add_entity_names(self, entity: Entity) -> None:
        keys = set()
        for entry in entity.all_names():
            key = normalize_name(entry.name)
            if not key:
                continue
            self._exact[key] = (entity.id, entry.name, entry.lang,
                                entry.name == entity.canonical)
            keys.add(key)
        self._by_entity[entity.id] = keys
        if self._postings_on:
            for key in keys:
                for bigram in _bigrams(key):
                    self._bigram_postings.setdefault(bigram, set()).add(key)

    def _maybe_enable_postings(self) -> None:
        """规模超过阈值时启用预过滤，并对全量名字补建倒排（一次性）。"""
        if not self._postings_on and len(self._exact) >= self._PREFILTER_MIN_NAMES:
            for key in self._exact:
                for bigram in _bigrams(key):
                    self._bigram_postings.setdefault(bigram, set()).add(key)
            self._postings_on = True
        elif self._postings_on and len(self._exact) < self._PREFILTER_MIN_NAMES // 2:
            # 规模回落明显时释放倒排（迟滞避免抖动）
            self._bigram_postings.clear()
            self._postings_on = False

    def remove_entity(self, entity_id: str) -> None:
        keys = self._by_entity.pop(entity_id, None)
        if keys is None:
            return
        for key in keys:
            self._exact.pop(key, None)
        if self._postings_on:
            for key in keys:
                for bigram in _bigrams(key):
                    posting = self._bigram_postings.get(bigram)
                    if posting is not None:
                        posting.discard(key)
                        if not posting:
                            del self._bigram_postings[bigram]

    @property
    def name_count(self) -> int:
        return len(self._exact)
