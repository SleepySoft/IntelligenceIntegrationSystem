"""EntityAlias V2 影子装配入口。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from EntityAliasV2.service import EntityAliasV2Service
from EntityAliasV2.shadow import (
    EntityAliasV2ShadowExtension,
    EntityAliasV2ShadowStore,
)
from EntityAliasV2.store import SQLiteAliasV2Store


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntityAliasV2ShadowHandles:
    service: EntityAliasV2Service
    observation_store: EntityAliasV2ShadowStore
    extension: EntityAliasV2ShadowExtension


def create_entity_alias_v2(config) -> EntityAliasV2Service | None:
    """按正式模式创建 V2 事实服务；shadow 观察可复用同一实例。"""

    mode = str(config.get("entity_alias_v2.mode", "shadow") or "shadow")
    shadow_enabled = bool(config.get("entity_alias_v2_shadow.enabled", False))
    if mode == "shadow" and not shadow_enabled:
        return None
    from GlobalConfig import DATA_PATH
    root = os.path.join(DATA_PATH, "entity_alias_v2")
    database = (
        config.get("entity_alias_v2.database", "")
        or config.get("entity_alias_v2_shadow.database", "")
        or os.path.join(root, "entities.db")
    )
    return EntityAliasV2Service(SQLiteAliasV2Store(os.path.abspath(database)))


def create_production_registry(config, *, service=None, legacy=None):
    from EntityAliasV2.production import (
        EntityAliasV2ProductionAdapter,
        EntityRegistryRouter,
        ProductionPolicy,
    )

    mode = str(config.get("entity_alias_v2.mode", "shadow") or "shadow")
    adapter = None
    if service is not None:
        adapter = EntityAliasV2ProductionAdapter(service, policy=ProductionPolicy(
            trust_wikidata=bool(config.get(
                "entity_alias_v2.trust_wikidata", True)),
            contextual_person_aliases=bool(config.get(
                "entity_alias_v2.contextual_person_aliases", True)),
            require_country_for_contextual_person=bool(config.get(
                "entity_alias_v2.require_country_for_contextual_person", True)),
            min_contextual_name_length=int(config.get(
                "entity_alias_v2.min_contextual_name_length", 2)),
        ))
    return EntityRegistryRouter(mode=mode, v2=adapter, legacy=legacy)


def _build_suggester(config, root: str):
    if not config.get("entity_alias_v2_shadow.wikidata.enabled", True):
        return None
    from EntityAlias.wikidata import (
        DEFAULT_UA,
        WikidataSuggester,
        proxy_uses_socks,
    )
    from GlobalConfig import APPLIED_PROXY

    proxy = config.get(
        "entity_alias_v2_shadow.wikidata.proxy", "") or APPLIED_PROXY or ""
    if proxy_uses_socks(proxy):
        try:
            import socks  # noqa: F401
        except ImportError:
            logger.warning("V2 shadow 代理为 socks 但 PySocks 未安装，将改为直连。")
            proxy = ""
    langs = config.get("entity_alias_v2_shadow.wikidata.langs", "zh,en")
    return WikidataSuggester(
        user_agent=(config.get(
            "entity_alias_v2_shadow.wikidata.user_agent", "")
            or DEFAULT_UA.replace("EntityAlias/1.0", "EntityAliasV2-Shadow/1.0")),
        proxy=proxy,
        min_interval=float(config.get(
            "entity_alias_v2_shadow.wikidata.min_interval_sec", 0.5)),
        timeout=float(config.get(
            "entity_alias_v2_shadow.wikidata.timeout_sec", 10.0)),
        cache_path=os.path.join(root, "wikidata_cache.sqlite"),
        langs=tuple(part.strip() for part in str(langs).split(",") if part.strip()),
    )


def create_entity_alias_v2_shadow(
    config,
    *,
    v1_service: Any = None,
    archive_collections=(),
    service: EntityAliasV2Service | None = None,
) -> EntityAliasV2ShadowHandles | None:
    """构造完全隔离的 V2 影子；未启用时返回 ``None``。"""

    if not config.get("entity_alias_v2_shadow.enabled", False):
        return None
    from GlobalConfig import DATA_PATH

    root = os.path.join(DATA_PATH, "entity_alias_v2")
    database = (
        service.store.path if service is not None
        else config.get("entity_alias_v2_shadow.database", "") or os.path.join(
            root, "entities.db")
    )
    observations = config.get(
        "entity_alias_v2_shadow.observations_database", "") or os.path.join(
            root, "shadow.db")
    database = os.path.abspath(database)
    observations = os.path.abspath(observations)
    if database == observations:
        raise ValueError("V2 事实库与影子观测库必须使用不同文件")

    if service is None:
        store = SQLiteAliasV2Store(database)
        service = EntityAliasV2Service(store)
    observation_store = EntityAliasV2ShadowStore(observations)
    extension = EntityAliasV2ShadowExtension(
        service,
        observation_store,
        v1_service=v1_service,
        suggester=_build_suggester(config, root),
        queue_size=int(config.get("entity_alias_v2_shadow.queue_size", 1000)),
        archive_collections=archive_collections,
        backfill_limit=int(config.get(
            "entity_alias_v2_shadow.backfill_limit", 1000)),
    )
    return EntityAliasV2ShadowHandles(
        service=service,
        observation_store=observation_store,
        extension=extension,
    )
