"""IIS 应用门面：组合运行时、领域流程和查询端口。"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ServiceComponent.adapters import IISPipelinePorts
from ServiceComponent.pipeline import ARCHIVE_REQUESTED, EventPipelinePlugin, INTAKE_RECEIVED
from ServiceComponent.runtime import HubEvent, HubPlugin, HubRuntime


class HubApplication:
    """主工程访问 Hub 的门面，不构造业务基础设施。

    调用方在组合根创建 registry、AI manager、扩展和端口。门面只将它们安装到
    runtime，并提供 Web adapter 所需的查询/提交端口。
    """

    def __init__(
            self,
            *,
            subsystem_registry: Any,
            ai_client_manager: Any,
            worker_count: int = 1,
            pipeline_ports: Any = None,
            runtime: Optional[HubRuntime] = None,
            extensions: Iterable[HubPlugin] = (),
            vector_search: Optional[Callable[..., List[Tuple[str, float, dict]]]] = None,
            services: Optional[Dict[str, Any]] = None,
    ):
        self.subsystem_registry = subsystem_registry
        self.ai_client_manager = ai_client_manager
        self.default_subsystem = subsystem_registry.default()
        if self.default_subsystem is None:
            raise ValueError("subsystem_registry 必须提供默认子系统。")
        self.default_subsystem_name = self.default_subsystem.name
        self.runtime = runtime or HubRuntime(worker_count=worker_count)
        ports = pipeline_ports or IISPipelinePorts(subsystem_registry, ai_client_manager)
        self.runtime.install(EventPipelinePlugin(ports, ports, ports))
        for extension in extensions:
            self.runtime.install(extension)

        self._vector_search = vector_search
        self.services = dict(services or {})

    def startup(self) -> None:
        self.runtime.start()

    def shutdown(self, timeout: float = 10.0) -> None:
        self.runtime.stop(timeout=timeout, drain=True)

    @property
    def statistics(self) -> Dict[str, Any]:
        """运行时通用统计；领域/运维扩展可在 services 中提供额外统计。"""
        return {"runtime": self.runtime.stats, "subsystems": self.subsystem_registry.describe()}

    def submit_collected_data(self, data: dict) -> bool:
        subsystem = str(data.get("subsystem") or "").strip() or self.default_subsystem_name
        if self.subsystem_registry.resolve(subsystem) is None:
            return False
        self.runtime.emit(HubEvent(INTAKE_RECEIVED, dict(data), subsystem))
        return True

    def submit_archived_data(self, data: dict) -> bool:
        appendix = data.get("APPENDIX") or {}
        subsystem = str(appendix.get("__SUBSYSTEM__") or data.get("subsystem") or "").strip()
        subsystem = subsystem or self.default_subsystem_name
        if self.subsystem_registry.resolve(subsystem) is None:
            return False
        self.runtime.emit(HubEvent(ARCHIVE_REQUESTED, dict(data), subsystem))
        return True

    def get_intelligence(self, intelligence_uuid, db: str = "archive",
                         light_weight: bool = False, subsystem: Optional[str] = None):
        ctx = self._context(subsystem)
        engine = ctx.cache_query_engine if db == "cache" else ctx.archive_query_engine
        return engine.get_intelligence(intelligence_uuid, light_weight=light_weight)

    def query_intelligence(self, *, db: str = "archive", subsystem: Optional[str] = None,
                           **kwargs):
        ctx = self._context(subsystem)
        engine = ctx.cache_query_engine if db == "cache" else ctx.archive_query_engine
        return engine.query_intelligence(**kwargs)

    def get_intelligence_summary(self, subsystem: Optional[str] = None):
        summary = self._context(subsystem).archive_query_engine.get_intelligence_summary()
        return summary["total_count"], summary["base_uuid"]

    def aggregate(self, pipeline: list, subsystem: Optional[str] = None):
        return self._context(subsystem).archive_query_engine.aggregate(pipeline)

    def count_documents(self, query: dict, subsystem: Optional[str] = None) -> int:
        return self._context(subsystem).archive_query_engine.count_documents(query)

    def get_statistics_engine(self, subsystem: Optional[str] = None):
        return self._context(subsystem).statistics_engine

    def get_query_engine(self, subsystem: Optional[str] = None):
        return self._context(subsystem).archive_query_engine

    def get_subsystems(self) -> List[Dict[str, Any]]:
        return self.subsystem_registry.describe()

    def get_prompt(self, subsystem: Optional[str] = None, version: Optional[int] = None) -> str:
        ctx = self._context(subsystem)
        self.subsystem_registry.refresh_prompts(ctx)
        if not ctx.prompt_table:
            return "[Prompt] Not configured."
        if version is not None:
            return ctx.prompt_table.get(int(version), f"[Prompt v{version}] Not configured.")
        return ctx.prompt_table[max(ctx.prompt_table)]

    def submit_intelligence_manual_rating(self, intelligence_uuid: str, rating: dict,
                                          subsystem: Optional[str] = None) -> bool:
        if not isinstance(rating, dict):
            return False
        self._context(subsystem).mongo_db_archive.update(
            {"UUID": intelligence_uuid}, {"APPENDIX.__MANUAL_RATING__": rating})
        return True

    def vector_search_intelligence(self, **kwargs) -> List[Tuple[str, float, dict]]:
        return self._vector_search(**kwargs) if self._vector_search else []

    def get_service(self, name: str, default: Any = None) -> Any:
        return self.services.get(name, default)

    def _context(self, subsystem: Optional[str]) -> Any:
        ctx = self.subsystem_registry.resolve(subsystem) or self.default_subsystem
        return ctx
