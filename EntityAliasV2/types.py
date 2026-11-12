"""V2 实体类型规范化。

``mention_type`` 是某次抽取使用的词值；本模块把兼容的旧词值投影为 V2 唯一的
``canonical_type``。这不是粗粒度 type family：不同 canonical type 不会因为同属
某个上层类别而被自动合并。
"""

from __future__ import annotations


CANONICAL_TYPES = frozenset({
    "person",
    "organization",
    "geopolitical_entity",
    "location",
    "facility",
    "equipment",
    "product",
    "resource",
    "asset",
    "information",
    "policy",
    "agreement",
    "position",
    "capability",
    "topic",
    "phenomenon",
    "case",
    "event",
    "other_object",
})

# 这里只收录“同一类型的不同词值”。organization/facility 等语义相近但身份不同的
# 类型不得放进来；它们必须交给上下文解析。
_TYPE_ALIASES = {
    "country": "geopolitical_entity",
    "nation": "geopolitical_entity",
    "state": "geopolitical_entity",
    "company": "organization",
    "government_agency": "organization",
    "place": "location",
}


def normalize_entity_type(value: str) -> str | None:
    """把抽取类型转换为 canonical type；未知或空类型返回 ``None``。"""

    key = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if not key:
        return None
    key = _TYPE_ALIASES.get(key, key)
    return key if key in CANONICAL_TYPES else None
