# VectorIntelligence/iis_integration.py
# 向量子系统的 IIS 装配入口：组合根（IntelligenceHubStartup）唯一需要 import 的模块。
#
# 职责：读取配置 → 构造 VectorDB 客户端与索引扩展 → 组装聚合/图谱延迟服务 →
# 返回 handles（插件列表、service locator 条目、search 门面、聚合触发器）。
# 配置未启用或构造失败时返回 None / 抛异常由组合根捕获，均不影响 IIS 主链路。

from __future__ import annotations

import datetime
import logging
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple

from MyPythonUtility.proc_utils import find_processes
from ServiceComponent.event_v4_archive import ArchivedDataV4
from ServiceComponent.intelligence_projections import IntelligenceProjectionProfile
from ServiceComponent.runtime import DeferredServicePlugin, HubPlugin
from VectorDB.VectorDBClient import VectorDBClient

from VectorIntelligence.aggregation import IntelligenceAggregationEngine, generate_aggregation_plan
from VectorIntelligence.config import load_vector_db_settings
from VectorIntelligence.extension import VectorIntelligenceExtension
from VectorIntelligence.graph import DynamicGraphEngine
from VectorIntelligence.vector_engine import IntelligenceVectorDBEngine

logger = logging.getLogger(__name__)


class VectorIntelligenceHandles(NamedTuple):
    """装配产物：组合根据此安装插件、登记 service locator、绑定搜索门面。"""

    extension: VectorIntelligenceExtension
    plugins: Tuple[HubPlugin, ...]
    services: Dict[str, Any]
    search: Callable[..., list]
    run_aggregation: Callable[[], None]
    vector_client: Any  # VectorDBClient，供 EntityAlias 等其它子系统复用


def _event_v4_record_factory(payload: Dict[str, Any]) -> ArchivedDataV4:
    document = dict(payload)
    document.pop("_id", None)
    document.pop("events", None)
    document.pop("entities", None)
    return ArchivedDataV4.model_validate(document)


def create_vector_intelligence(config, *, domain_resources=None,
                               subsystem_registry=None) -> Optional[VectorIntelligenceHandles]:
    """按配置装配向量子系统；未启用时返回 None。"""
    resources = domain_resources or subsystem_registry
    settings = load_vector_db_settings(config)
    if settings is None:
        return None

    # VectorDB 是独立进程：此处仅探测并记录，不启动、不等待（就绪等待在扩展后台线程）。
    pids = find_processes('VectorDBBService.py')
    if pids:
        logger.info(f"Found running vector db service {', '.join(str(p) for p in pids)}, ignore.")

    vector_db_client = VectorDBClient(settings["url"])

    vector_extension = VectorIntelligenceExtension(
        vector_db_client,
        default_domain=resources.default_name,
        engine_factory=IntelligenceVectorDBEngine,
        record_factory=_event_v4_record_factory,
    )

    def create_aggregation_service():
        service = IntelligenceAggregationEngine(
            vector_db_client, generate_aggregation_plan(profile="agglomerative_strict"))
        service.ensure_plan(overwrite=True)
        return service

    def run_aggregation():
        service = aggregation_extension.service
        if service is None:
            return
        query_engine = resources.default().event_v4_query_engine
        latest = query_engine.get_latest_archive_timestamp()
        period = None
        if latest and datetime.datetime.now().timestamp() - latest >= 24 * 3600:
            period = (latest - 24 * 3600, latest)
        service.trigger_offline(
            overrides=None, time_range=period,
            doc_fetcher=lambda identifiers: query_engine.get_intelligence(
                identifiers, profile=IntelligenceProjectionProfile.CARD))

    aggregation_extension = DeferredServicePlugin(
        lambda: vector_extension.ready,
        create_aggregation_service,
        name="summary-aggregation",
        on_ready=lambda _: run_aggregation(),
    )
    graph_extension = DeferredServicePlugin(
        lambda: vector_extension.ready,
        lambda: DynamicGraphEngine(
            mongo_db=resources.default().event_v4_intelligence_storage,
            query_engine=resources.default().event_v4_query_engine,
            vector_engine=vector_extension.summary_engine,
            ai_client=None,
        ),
        name="dynamic-graph",
    )

    return VectorIntelligenceHandles(
        extension=vector_extension,
        plugins=(vector_extension, aggregation_extension, graph_extension),
        services={
            "vector": vector_extension,
            "aggregation": aggregation_extension,
            "dynamic_graph": graph_extension,
        },
        search=vector_extension.search,
        run_aggregation=run_aggregation,
        vector_client=vector_db_client,
    )


def mount_to_iis(app, *, config, hub, hub_service, wrapper, doc_cleaner,
                 handles: Optional[VectorIntelligenceHandles] = None,
                 url_prefix: str = '/vector'):
    """把 /vector/ Blueprint 挂载到 IIS Flask app（Web 侧装配，失败不影响主站）。

    同时把向量搜索的单一实现注入 Core Web 的委托点（set_vector_search_handler），
    旧 /intelligences/query?search_mode=vector_* 入口由此与新实现共用一份代码。
    """
    if not config.get('intelligence_hub.vectordb.enabled', False):
        return None
    try:
        from VectorIntelligence.search import build_vector_search_handler
        from VectorIntelligence.web import VectorWebService

        service = VectorWebService(
            hub=hub, hub_service=hub_service, doc_cleaner=doc_cleaner, handles=handles)
        service.mount_to_app(app, wrapper=wrapper, url_prefix=url_prefix)
        hub_service.set_vector_search_handler(build_vector_search_handler(hub=hub))
        return service
    except Exception:
        logger.exception("向量子系统 Web 挂载失败，IIS 主站继续运行。")
        return None
