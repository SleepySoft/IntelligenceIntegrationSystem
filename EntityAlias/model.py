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

# 实体类型词表（自由扩展，未知类型也会独立成文件存储）
KNOWN_TYPES = ("person", "organization", "country", "location",
               "animal", "commodity", "other", "uncertain")

# 别名角色：空=一般别名；abbr=简称；full=全称；former=旧称
# （native 已废弃：母语/外语的“标准名”不再是别名，应放入 Entity.localized）
KNOWN_ROLES = ("", "abbr", "full", "former", "native")

# 实体状态：unverified=AI 注册未经确认；verified=已核实（权威）
KNOWN_STATUS = ("unverified", "verified")


class AliasError(Exception):
    """业务错误，code 供 API 映射 HTTP 状态码。"""

    def __init__(self, message: str, code: str = "bad_request"):
        super().__init__(message)
        self.code = code


@dataclass
class AliasEntry:
    name: str
    lang: str = ""
    role: str = ""  # abbr / full / former / native / 空

    def to_dict(self) -> Dict[str, Any]:
        d = {"name": self.name, "lang": self.lang}
        if self.role:
            d["role"] = self.role
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "AliasEntry":
        return AliasEntry(name=display_normalize(d.get("name", "")),
                          lang=str(d.get("lang", "") or "").strip().lower(),
                          role=str(d.get("role", "") or "").strip().lower())


@dataclass
class Entity:
    id: str
    canonical: str
    lang: str = "zh"
    type: str = "other"
    aliases: List[AliasEntry] = field(default_factory=list)
    localized: Dict[str, str] = field(default_factory=dict)  # lang -> 该语言的标准名
    native_lang: str = ""      # 母语代码；空 = 与主标准名同语言
    description: str = ""
    status: str = "unverified"
    refs: Dict[str, str] = field(default_factory=dict)  # 外部标识：{"wikidata": "Q9684", ...}
    created_at: str = ""
    updated_at: str = ""

    # ------------------------------------------------------------------ 构造

    @staticmethod
    def new(canonical: str, lang: str = "zh", type: str = "other",
            aliases: List[AliasEntry] = None, description: str = "",
            status: str = "unverified", localized: Dict[str, str] = None,
            native_lang: str = "", refs: Dict[str, str] = None) -> "Entity":
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return Entity(
            id="ent_" + uuid.uuid4().hex[:12],
            canonical=display_normalize(canonical),
            lang=(lang or "").strip().lower(),
            type=(type or "other").strip().lower(),
            aliases=list(aliases or []),
            localized={str(k).strip().lower(): display_normalize(v)
                       for k, v in (localized or {}).items()},
            native_lang=(native_lang or "").strip().lower(),
            description=str(description or "").strip(),
            status=(status or "unverified").strip().lower(),
            refs={str(k).strip().lower(): str(v).strip()
                  for k, v in (refs or {}).items()},
            created_at=now,
            updated_at=now,
        )

    def touch(self) -> None:
        self.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # ------------------------------------------------------------------ 名称视图

    def display_name(self, lang: str = "") -> str:
        """按语言返回标准名：指定语言无对应版本时回落主标准名。lang='native' 表示母语。"""
        lang = (lang or "").strip().lower()
        if lang == "native":
            lang = self.native_lang or self.lang
        if not lang or lang == self.lang:
            return self.canonical
        return self.localized.get(lang, self.canonical)

    def all_names(self) -> List[AliasEntry]:
        """主标准名 + 各语言标准名 + 全部别名。主标准名恒为第一个。"""
        entries = [AliasEntry(self.canonical, self.lang)]
        entries.extend(AliasEntry(name, lang) for lang, name in self.localized.items())
        entries.extend(self.aliases)
        return entries

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
            "localized": dict(self.localized),
            "native_lang": self.native_lang,
            "description": self.description,
            "status": self.status,
            "refs": dict(self.refs),
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
            localized={str(k).strip().lower(): display_normalize(v)
                       for k, v in (d.get("localized") or {}).items()},
            native_lang=str(d.get("native_lang", "") or "").strip().lower(),
            description=str(d.get("description", "") or ""),
            status=str(d.get("status", "unverified") or "unverified").strip().lower(),
            refs={str(k).strip().lower(): str(v).strip()
                  for k, v in (d.get("refs") or {}).items()},
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
        )

    # ------------------------------------------------------------------ 校验

    def validate(self) -> None:
        if not self.canonical:
            raise AliasError("标准名不能为空", "bad_request")
        if not normalize_name(self.canonical):
            raise AliasError("标准名归一化后为空", "bad_request")
        if self.status not in KNOWN_STATUS:
            raise AliasError(f"非法状态：{self.status!r}（可选：{KNOWN_STATUS}）", "bad_request")
        seen = set()
        for a in self.aliases:
            if not a.name:
                raise AliasError("别名不能为空", "bad_request")
            if a.role not in KNOWN_ROLES:
                raise AliasError(f"非法别名角色：{a.role!r}（可选：{KNOWN_ROLES}）", "bad_request")
            key = normalize_name(a.name)
            if not key:
                raise AliasError(f"别名 {a.name!r} 归一化后为空", "bad_request")
            if key in seen:
                raise AliasError(f"别名重复：{a.name!r}", "bad_request")
            seen.add(key)
        for lang, name in self.localized.items():
            if not lang:
                raise AliasError("localized 语言代码不能为空", "bad_request")
            key = normalize_name(name)
            if not key:
                raise AliasError(f"localized[{lang!r}] 归一化后为空", "bad_request")
            if key in seen:
                raise AliasError(f"localized[{lang!r}] 与别名重复", "bad_request")
            seen.add(key)
        if normalize_name(self.canonical) in seen:
            raise AliasError("别名/语言标准名与主标准名重复", "bad_request")
