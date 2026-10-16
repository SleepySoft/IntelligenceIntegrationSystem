# EntityAlias/__init__.py
# 实体别名子系统：标准名 <-> 别名 映射、批量解析、模糊/部分查询、可选向量召回。
# 装配入口刻意放在 iis_integration.py（同 SituationViews/VectorIntelligence 惯例）。

from EntityAlias.model import AliasEntry, AliasError, Entity
from EntityAlias.service import EntityAliasService
from EntityAlias.store import JSONLStore

__all__ = [
    "AliasEntry",
    "AliasError",
    "Entity",
    "EntityAliasService",
    "JSONLStore",
]
