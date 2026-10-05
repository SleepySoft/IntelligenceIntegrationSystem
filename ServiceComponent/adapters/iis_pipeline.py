"""当前 IIS 新闻式流程到通用 Pipeline 端口的适配器。"""

from __future__ import annotations

import datetime
import random
from typing import Any, Callable, Optional

from ServiceComponent.IntelligenceHubDefines_v2 import (
    APPENDIX_ARCHIVED_FLAG,
    APPENDIX_SUBSYSTEM,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TIME_DONE,
    APPENDIX_TIME_GOT,
    APPENDIX_TIME_POST,
    APPENDIX_TIME_PUB,
    APPENDIX_AI_MODEL,
    APPENDIX_AI_SERVICE,
    APPENDIX_PROMPT_VERSION,
    APPENDIX_TOTAL_SCORE,
    ARCHIVED_FLAG_ARCHIVED,
    ARCHIVED_FLAG_DROP,
    ARCHIVED_FLAG_DUPLICATED,
    ArchivedData,
    CollectedData,
)
from ServiceComponent.pipeline.contracts import StageResult
from ServiceComponent.runtime.events import HubEvent
from Tools.DateTimeUtility import get_aware_time, time_digit_list_to_datetime, time_str_to_datetime
from MyPythonUtility.DictTools import check_sanitize_dict


class IISPipelinePorts:
    """IIS 既有新闻数据路径的端口实现。

    这是 adapter，不是 runtime：它知道 Pydantic v2、Mongo 资源、Prompt、评分器和
    AIClientManager。未来领域若有不可归一化的结构，只需实现自己的三个端口。
    """

    def __init__(
            self,
            subsystem_registry: Any,
            ai_client_manager: Any,
            *,
            analyzer: Optional[Callable[[Any, str, dict], dict]] = None,
            scorer_factory: Callable[[Optional[dict]], Any] = None,
    ):
        self._registry = subsystem_registry
        self._ai_client_manager = ai_client_manager
        self._analyzer = analyzer or self._load_default_analyzer()
        self._scorer_factory = scorer_factory or self._load_default_scorer_factory()

    @staticmethod
    def _load_default_scorer_factory() -> Callable[[Optional[dict]], Any]:
        """评分器也仅在真实 IIS 组合时加载。"""
        from ServiceComponent.IntelligenceScoringEngine import IntelligenceScoringEngine

        def build(config: Optional[dict]) -> IntelligenceScoringEngine:
            return IntelligenceScoringEngine(config=config) if config else IntelligenceScoringEngine()

        return build

    @staticmethod
    def _load_default_analyzer() -> Callable[[Any, str, dict], dict]:
        """仅在真实 IIS 组合时加载 AI 输出解析依赖。"""
        from ServiceComponent.IntelligenceAnalyzerProxy import analyze_with_ai
        return analyze_with_ai

    def accept(self, event: HubEvent) -> StageResult:
        ctx = self._resolve_context(event)
        data, error = check_sanitize_dict(dict(event.payload), CollectedData)
        if error:
            return StageResult.reject(error)
        data.setdefault("subsystem", ctx.name)
        data.setdefault("collect_time", get_aware_time())
        data[APPENDIX_TIME_POST] = get_aware_time()

        if self._is_duplicated(data, "informant", ctx.cache_query_engine):
            return StageResult.reject("collected_data_duplicated")
        if ctx.mongo_db_cache:
            ctx.mongo_db_cache.insert(data)
        return StageResult.allow(data, subsystem=ctx.name)

    def analyze(self, event: HubEvent) -> StageResult:
        ctx = self._resolve_context(event)
        original_data = dict(event.payload)
        if self._is_duplicated(original_data, "INFORMANT", ctx.archive_query_engine):
            return StageResult.reject("already_archived")
        if not ctx.prompt_table:
            return StageResult.reject("prompt_not_configured")

        prompt_version = random.choice(list(ctx.prompt_table.keys()))
        ai_client = self._ai_client_manager.get_available_client(
            f"HubRuntime-{event.correlation_id or event.event_id}")
        if ai_client is None:
            return StageResult.reject("no_available_ai_client")

        try:
            result = self._analyzer(ai_client, ctx.prompt_table[prompt_version], original_data)
        finally:
            self._ai_client_manager.release_client(ai_client)
        if not isinstance(result, dict):
            return StageResult.reject("analysis_result_not_dict")
        if result.get("error"):
            return StageResult.reject(f"analysis_failed:{result['error']}")
        if self._is_low_value(result):
            self._mark_cache(original_data.get("UUID", ""), ARCHIVED_FLAG_DROP, ctx)
            return StageResult.reject("low_value")

        result["UUID"] = str(original_data.get("UUID", "")).strip()
        result["INFORMANT"] = str(original_data.get("informant", "")).strip()
        appendix = result.setdefault("APPENDIX", {})
        ai_appendix = dict(appendix) if isinstance(appendix, dict) else {}
        result["APPENDIX"] = {
            **ai_appendix,
            APPENDIX_PROMPT_VERSION: prompt_version,
            APPENDIX_AI_SERVICE: ai_client.get_api_base_url(),
            APPENDIX_AI_MODEL: ai_client.get_current_model(),
            APPENDIX_SUBSYSTEM: ctx.name,
        }
        self._copy_timestamps(original_data, result)
        result["APPENDIX"][APPENDIX_TOTAL_SCORE] = self._scorer_factory(
            getattr(ctx, "scoring_config", None)).calculate_single(result)

        validated, error = check_sanitize_dict(result, ArchivedData)
        if error:
            return StageResult.reject(error)
        validated["RAW_DATA"] = original_data
        validated["SUBMITTER"] = "HubRuntime analysis adapter"
        return StageResult.allow(validated, subsystem=ctx.name)

    def archive(self, event: HubEvent) -> StageResult:
        ctx = self._resolve_context(event)
        data = dict(event.payload)
        if self._is_duplicated(data, "INFORMANT", ctx.archive_query_engine):
            self._mark_cache(data.get("UUID", ""), ARCHIVED_FLAG_DUPLICATED, ctx)
            return StageResult.reject("archive_duplicated")
        if self._is_low_value(data):
            if ctx.mongo_db_low_value:
                ctx.mongo_db_low_value.insert(data)
            self._mark_cache(data.get("UUID", ""), ARCHIVED_FLAG_DROP, ctx)
            return StageResult.reject("low_value")

        data.setdefault("APPENDIX", {})[APPENDIX_TIME_ARCHIVED] = get_aware_time()
        if ctx.mongo_db_archive:
            ctx.mongo_db_archive.insert(data)
        self._mark_cache(data.get("UUID", ""), ARCHIVED_FLAG_ARCHIVED, ctx)
        return StageResult.allow(data, subsystem=ctx.name)

    def _resolve_context(self, event: HubEvent) -> Any:
        ctx = self._registry.resolve(event.subsystem)
        if ctx is None:
            raise ValueError(f"unknown_subsystem:{event.subsystem!r}")
        self._registry.refresh_prompts(ctx)
        return ctx

    @staticmethod
    def _is_low_value(data: dict) -> bool:
        return "EVENT_TEXT" not in data

    @staticmethod
    def _is_duplicated(data: dict, informant_key: str, query_engine: Any) -> bool:
        if query_engine is None:
            return False
        target_uuid = str(data.get("UUID", "")).strip()
        target_informant = str(data.get("informant") or data.get("INFORMANT") or "").strip()
        if not target_uuid or not target_informant:
            return False
        conditions = {"UUID": target_uuid, informant_key: target_informant}
        return bool(query_engine.common_query(conditions=conditions, operator="$or"))

    @staticmethod
    def _mark_cache(uuid: str, state: str, ctx: Any) -> None:
        if uuid and ctx.mongo_db_cache:
            ctx.mongo_db_cache.update(
                {"UUID": uuid}, {f"APPENDIX.{APPENDIX_ARCHIVED_FLAG}": state})

    @staticmethod
    def _copy_timestamps(original_data: dict, processed_data: dict) -> None:
        appendix = processed_data.setdefault("APPENDIX", {})
        pub_time = original_data.get("pub_time")
        appendix[APPENDIX_TIME_PUB] = (
            time_digit_list_to_datetime(pub_time) or time_str_to_datetime(pub_time) or pub_time
            if pub_time else None)
        appendix[APPENDIX_TIME_GOT] = original_data.get("collect_time")
        appendix[APPENDIX_TIME_POST] = original_data.get(APPENDIX_TIME_POST)
        appendix[APPENDIX_TIME_DONE] = get_aware_time()
