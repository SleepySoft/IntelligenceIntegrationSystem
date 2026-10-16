# EntityAlias/model.py
# 实体数据模型：一个标准名（带语言标签）+ 若干别名（各带语言标签）。
#
# 语言标签约定：标准名是系统内权威展示/连接名（通常取中文名；无中文译名时
# 取最通行名并标注其语言）；英文名、母语本名一律作为带 lang 标签的别名。
# 解析跨语言：任何语言的名字都映射到同一标准名。

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

from EntityAlias.normalize import display_normalize, normalize_name

# 常见实体类型（自由扩展，未知类型也会独立成文件存储）
KNOWN_TYPES = ("person", "organization", "location", "other")


class AliasError(Exception):
    """业务错误，code 供 API 映射 HTTP 状态码。"""

    def __init__(self, message: str, code: str = "bad_request"):
        super().__init__(message)
        self.code = code


@dataclass
class AliasEntry:
    name: str
    lang: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "lang": self.lang}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "AliasEntry":
        return AliasEntry(name=display_normalize(d.get("name", "")),
                          lang=str(d.get("lang", "") or "").strip().lower())


@dataclass
class Entity:
    id: str
    canonical: str
    lang: str = "zh"
    type: str = "other"
    aliases: List[AliasEntry] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    # ------------------------------------------------------------------ 构造

    @staticmethod
    def new(canonical: str, lang: str = "zh", type: str = "other",
            aliases: List[AliasEntry] = None) -> "Entity":
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return Entity(
            id="ent_" + uuid.uuid4().hex[:12],
            canonical=display_normalize(canonical),
            lang=(lang or "").strip().lower(),
            type=(type or "other").strip().lower(),
            aliases=list(aliases or []),
            created_at=now,
            updated_at=now,
        )

    def touch(self) -> None:
        self.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # ------------------------------------------------------------------ 名称视图

    def all_names(self) -> List[AliasEntry]:
        """标准名 + 全部别名。标准名恒为第一个。"""
        return [AliasEntry(self.canonical, self.lang)] + list(self.aliases)

    def normalized_keys(self) -> List[str]:
        return [normalize_name(e.name) for e in self.all_names() if normalize_name(e.name)]

    # ------------------------------------------------------------------ 序列化

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "canonical": self.canonical,
            "lang": self.lang,
            "type": self.type,
            "aliases": [a.to_dict() for a in self.aliases],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Entity":
        return Entity(
            id=str(d["id"]),
            canonical=display_normalize(d.get("canonical", "")),
            lang=str(d.get("lang", "") or "").strip().lower(),
            type=str(d.get("type", "other") or "other").strip().lower(),
            aliases=[AliasEntry.from_dict(a) for a in d.get("aliases", [])],
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
        )

    # ------------------------------------------------------------------ 校验

    def validate(self) -> None:
        if not self.canonical:
            raise AliasError("标准名不能为空", "bad_request")
        if not normalize_name(self.canonical):
            raise AliasError("标准名归一化后为空", "bad_request")
        seen = set()
        for a in self.aliases:
            if not a.name:
                raise AliasError("别名不能为空", "bad_request")
            key = normalize_name(a.name)
            if not key:
                raise AliasError(f"别名 {a.name!r} 归一化后为空", "bad_request")
            if key in seen:
                raise AliasError(f"别名重复：{a.name!r}", "bad_request")
            seen.add(key)
        if normalize_name(self.canonical) in seen:
            raise AliasError("别名与标准名重复", "bad_request")
