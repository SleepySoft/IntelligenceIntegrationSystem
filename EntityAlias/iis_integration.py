# EntityAlias/iis_integration.py
# IIS 装配入口（组合根唯一需要 import 的模块，模式同 SituationViews/VectorIntelligence）。

from __future__ import annotations

import logging
import os
import threading

from EntityAlias.service import EntityAliasService
from EntityAlias.store import JSONLStore
from EntityAlias.vector import NullNearestIndex, VectorDBNearestIndex

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
VECTOR_COLLECTION_NAME = "entity_alias_names"


def _build_suggester(config, data_dir: str):
    """按配置构造 Wikidata 在线建议源；未启用或构造失败返回 None。"""
    if not config.get("entity_alias.wikidata.enabled", True):
        return None
    try:
        from GlobalConfig import DEFAULT_PROXY
        from EntityAlias.wikidata import DEFAULT_UA, WikidataSuggester

        proxy = config.get("entity_alias.wikidata.proxy", "") or DEFAULT_PROXY or ""
        if proxy.startswith("socks"):
            try:
                import socks  # noqa: F401（PySocks 未安装则 socks 代理不可用）
            except ImportError:
                logger.warning("代理为 socks 但 PySocks 未安装，Wikidata 建议源将直连。")
                proxy = ""
        langs = config.get("entity_alias.wikidata.langs", "zh,en")
        return WikidataSuggester(
            user_agent=config.get("entity_alias.wikidata.user_agent", "") or DEFAULT_UA,
            proxy=proxy,
            min_interval=float(config.get("entity_alias.wikidata.min_interval_sec", 0.5)),
            timeout=float(config.get("entity_alias.wikidata.timeout_sec", 10.0)),
            cache_path=os.path.join(data_dir, ".cache", "wikidata_cache.sqlite"),
            langs=tuple(x.strip() for x in str(langs).split(",") if x.strip()),
        )
    except Exception:
        logger.warning("Wikidata 建议源构造失败，在线建议关闭。", exc_info=True)
        return None


def mount_to_iis(app, *, config, wrapper, vector_client=None,
                 url_prefix: str = None) -> EntityAliasService | None:
    """把实体别名子系统挂载到 IIS Flask app。失败只记日志，返回 None。"""
    if not config.get("entity_alias.enabled", True):
        return None
    try:
        data_dir = config.get("entity_alias.data_dir", "") or DEFAULT_DATA_DIR
        prefix = url_prefix or config.get("entity_alias.url_prefix", "/entity-alias")

        # 可选向量化：复用 IIS 的 VectorDB 服务，独立集合，就绪前后台重试。
        vector_index = NullNearestIndex()
        vector_ok = config.get("entity_alias.vector_enabled", True) \
            and vector_client is not None
        if vector_ok:
            try:
                collection = vector_client.create_collection(
                    name=VECTOR_COLLECTION_NAME, chunk_size=256, chunk_overlap=0)
                vector_index = VectorDBNearestIndex(collection)
            except Exception:
                logger.warning("EntityAlias 向量集合创建失败，向量能力关闭。", exc_info=True)

        service = EntityAliasService(JSONLStore(data_dir), vector_index,
                                     suggester=_build_suggester(config, data_dir))

        from EntityAlias.web import EntityAliasWebService
        EntityAliasWebService(service).mount_to_app(app, wrapper=wrapper, url_prefix=prefix)

        if vector_index.available:
            def _sync_when_ready():
                try:
                    vector_client.wait_until_ready(timeout=10.0, poll_interval=2.0)
                    synced = service.rebuild_vector()
                    logger.info("EntityAlias 向量索引同步完成：%d 个名字", synced)
                except Exception:
                    logger.warning("EntityAlias 向量索引同步失败（不影响精确/模糊查询）。",
                                   exc_info=True)

            threading.Thread(target=_sync_when_ready,
                             name="EntityAliasVectorSync", daemon=True).start()

        return service
    except Exception:
        logger.exception("EntityAlias 挂载失败，IIS 主服务继续运行。")
        return None
