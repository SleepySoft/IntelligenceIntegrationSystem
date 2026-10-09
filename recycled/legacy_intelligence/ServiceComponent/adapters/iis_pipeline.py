"""当前 IIS 新闻式流程到通用 Pipeline 端口的适配器。"""

from __future__ import annotations

import datetime
import logging
import random
import threading
from typing import Any, Callable, Optional

from tenacity import (
    Retrying,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)

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
    APPENDIX_PROMPT_OVERRIDE,
    APPENDIX_TOTAL_SCORE,
    ARCHIVED_FLAG_ARCHIVED,
    ARCHIVED_FLAG_DROP,
    ARCHIVED_FLAG_DUPLICATED,
    ARCHIVED_FLAG_ERROR,
    ARCHIVED_FLAG_SENSITIVE,
    ArchivedData,
    CollectedData,
)
from ServiceComponent.pipeline.contracts import StageResult
from ServiceComponent.runtime.events import HubEvent
from ServiceComponent.manual_debug_analysis import MANUAL_TEST_SOURCE
from Tools.DateTimeUtility import get_aware_time, time_digit_list_to_datetime, time_str_to_datetime
from MyPythonUtility.DictTools import check_sanitize_dict


logger = logging.getLogger(__name__)


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
            transient_analyzer: Optional[Callable[[Any, str, dict], dict]] = None,
            scorer_factory: Callable[[Optional[dict]], Any] = None,
            retry_wait: Any = None,
            client_wait_interval: float = 1.0,
    ):
        if client_wait_interval <= 0:
            raise ValueError("client_wait_interval 必须大于 0。")
        self._registry = subsystem_registry
        self._ai_client_manager = ai_client_manager
        self._analyzer = analyzer or self._load_default_analyzer()
        self._transient_analyzer = (
            transient_analyzer
            or (analyzer if analyzer is not None else self._load_default_transient_analyzer())
        )
        self._scorer_factory = scorer_factory or self._load_default_scorer_factory()
        self._stop_event = threading.Event()
        self._dedupe_lock = threading.RLock()
        self._retry_wait = retry_wait or wait_exponential(multiplier=1, min=1, max=30)
        self._client_wait_interval = client_wait_interval

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

    @staticmethod
    def _load_default_transient_analyzer() -> Callable[[Any, str, dict], dict]:
        from ServiceComponent.IntelligenceAnalyzerProxy import analyze_with_ai_transient
        return analyze_with_ai_transient

    def accept(self, event: HubEvent) -> StageResult:
        ctx = self._resolve_context(event)
        data, error = check_sanitize_dict(dict(event.payload), CollectedData)
        if error:
            return StageResult.reject(error)
        if data.get("source") == MANUAL_TEST_SOURCE:
            return StageResult.reject("manual_test_requires_debug_route")
        data.setdefault("subsystem", ctx.name)
        data.setdefault("collect_time", get_aware_time())
        data[APPENDIX_TIME_POST] = get_aware_time()

        # 查询与插入必须在同一临界区，避免多个分析 worker 同时接收同一记录。
        with self._dedupe_lock:
            if self._is_duplicated(data, "informant", ctx.cache_query_engine):
                return StageResult.reject("collected_data_duplicated")
            if ctx.mongo_db_cache:
                ctx.mongo_db_cache.insert(data)
        return StageResult.allow(data, subsystem=ctx.name)

    def analyze(self, event: HubEvent) -> StageResult:
        return self._analyze(event, persist=True)

    def analyze_transient(self, event: HubEvent) -> StageResult:
        """执行完整分析与校验，但不读写任何业务数据库或统计计数。"""
        if dict(event.payload).get("source") != MANUAL_TEST_SOURCE:
            return StageResult.reject("transient_analysis_requires_manual_test_source")
        return self._analyze(event, persist=False)

    def get_transient_prompt_table(self, subsystem: str) -> dict[int, str]:
        ctx = self._registry.resolve(subsystem)
        if ctx is None:
            raise ValueError(f"unknown_subsystem:{subsystem!r}")
        self._registry.refresh_prompts(ctx)
        return dict(ctx.prompt_table)

    def _analyze(self, event: HubEvent, *, persist: bool) -> StageResult:
        ctx = self._resolve_context(event)
        original_data = dict(event.payload)
        if persist and self._is_duplicated(original_data, "INFORMANT", ctx.archive_query_engine):
            self._mark_cache(original_data.get("UUID", ""), ARCHIVED_FLAG_DUPLICATED, ctx)
            self._increment_stat(ctx, "dropped")
            return StageResult.reject("already_archived")
        if not ctx.prompt_table:
            if persist:
                self._mark_cache(original_data.get("UUID", ""), ARCHIVED_FLAG_ERROR, ctx)
                self._increment_stat(ctx, "error")
            return StageResult.reject("prompt_not_configured")

        try:
            requested_prompt_version, prompt_override = (
                self._transient_prompt_options(original_data) if not persist else (None, None)
            )
            result, prompt_version, ai_service, ai_model = self._analyze_with_retry(
                ctx, event, original_data,
                analyzer=self._analyzer if persist else self._transient_analyzer,
                prompt_version=requested_prompt_version,
                prompt_override=prompt_override)
            if not isinstance(result, dict):
                raise TypeError("analysis_result_not_dict")
            if result.get("error"):
                state = (ARCHIVED_FLAG_SENSITIVE
                         if result.get("api_error_code") == "HTTP_400"
                         else ARCHIVED_FLAG_ERROR)
                if persist:
                    self._mark_cache(original_data.get("UUID", ""), state, ctx)
                    self._increment_stat(ctx, "error")
                return StageResult.reject(f"analysis_failed:{result['error']}")

            result["UUID"] = str(original_data.get("UUID", "")).strip()
            result["INFORMANT"] = str(original_data.get("informant", "")).strip()
            appendix = result.setdefault("APPENDIX", {})
            ai_appendix = dict(appendix) if isinstance(appendix, dict) else {}
            result["APPENDIX"] = {
                **ai_appendix,
                APPENDIX_PROMPT_VERSION: prompt_version,
                APPENDIX_AI_SERVICE: ai_service,
                APPENDIX_AI_MODEL: ai_model,
                APPENDIX_SUBSYSTEM: ctx.name,
            }
            if not persist:
                result["APPENDIX"][APPENDIX_PROMPT_OVERRIDE] = bool(prompt_override)
            self._copy_timestamps(original_data, result)

            if self._is_low_value(result):
                low_value, error = check_sanitize_dict(result, ArchivedData)
                if error:
                    raise ValueError(error)
                if persist:
                    if ctx.mongo_db_low_value:
                        ctx.mongo_db_low_value.insert(low_value)
                    self._mark_cache(original_data.get("UUID", ""), ARCHIVED_FLAG_DROP, ctx)
                    self._increment_stat(ctx, "dropped")
                    return StageResult.reject("low_value")
                low_value["RAW_DATA"] = original_data
                low_value["SUBMITTER"] = "Manual transient debug"
                return StageResult.allow(
                    low_value, subsystem=ctx.name, transient=True, low_value=True)

            result["APPENDIX"][APPENDIX_TOTAL_SCORE] = self._scorer_factory(
                getattr(ctx, "scoring_config", None)).calculate_single(result)
            validated, error = check_sanitize_dict(result, ArchivedData)
            if error:
                raise ValueError(error)
            validated["RAW_DATA"] = original_data
            validated["SUBMITTER"] = (
                "Manual transient debug" if not persist else "HubRuntime analysis adapter"
            )
            return StageResult.allow(
                validated, subsystem=ctx.name, transient=not persist, low_value=False)
        except Exception as exc:
            if persist:
                self._mark_cache(original_data.get("UUID", ""), ARCHIVED_FLAG_ERROR, ctx)
                self._increment_stat(ctx, "error")
            return StageResult.reject(f"analysis_exception:{type(exc).__name__}:{exc}")

    def archive(self, event: HubEvent) -> StageResult:
        ctx = self._resolve_context(event)
        raw_data = dict(event.payload)
        data, error = check_sanitize_dict(raw_data, ArchivedData)
        if error:
            self._mark_cache(raw_data.get("UUID", ""), ARCHIVED_FLAG_ERROR, ctx)
            self._increment_stat(ctx, "error")
            return StageResult.reject(error)
        data.setdefault("APPENDIX", {})[APPENDIX_SUBSYSTEM] = ctx.name

        try:
            # 归档去重和写入同样需要原子化，防止多个 worker 重复落库。
            with self._dedupe_lock:
                if self._is_duplicated(data, "INFORMANT", ctx.archive_query_engine):
                    self._mark_cache(data.get("UUID", ""), ARCHIVED_FLAG_DUPLICATED, ctx)
                    self._increment_stat(ctx, "dropped")
                    return StageResult.reject("archive_duplicated")
                if self._is_low_value(data):
                    if ctx.mongo_db_low_value:
                        ctx.mongo_db_low_value.insert(data)
                    self._mark_cache(data.get("UUID", ""), ARCHIVED_FLAG_DROP, ctx)
                    self._increment_stat(ctx, "dropped")
                    return StageResult.reject("low_value")

                data["APPENDIX"][APPENDIX_TIME_ARCHIVED] = get_aware_time()
                if ctx.mongo_db_archive:
                    ctx.mongo_db_archive.insert(data)
                self._mark_cache(data.get("UUID", ""), ARCHIVED_FLAG_ARCHIVED, ctx)
            self._increment_stat(ctx, "archived")
            return StageResult.allow(data, subsystem=ctx.name)
        except Exception as exc:
            self._mark_cache(data.get("UUID", ""), ARCHIVED_FLAG_ERROR, ctx)
            self._increment_stat(ctx, "error")
            return StageResult.reject(f"archive_exception:{type(exc).__name__}:{exc}")

    def stop(self) -> None:
        self._stop_event.set()

    def _analyze_with_retry(self, ctx: Any, event: HubEvent,
                            original_data: dict, *, analyzer=None,
                            prompt_version: int | None = None,
                            prompt_override: str | None = None) -> tuple[dict, int, str, str]:
        if prompt_version is None:
            prompt_version = random.choice(list(ctx.prompt_table.keys()))
        elif prompt_version not in ctx.prompt_table:
            raise ValueError(f"prompt_version_not_configured:{prompt_version}")
        prompt = prompt_override if prompt_override is not None else ctx.prompt_table[prompt_version]
        client_metadata = {"service": "", "model": ""}
        user_name = f"HubRuntime-{event.correlation_id or event.event_id}"

        def analyze_once():
            ai_client = self._wait_for_ai_client(ctx, user_name)
            try:
                client_metadata["service"] = ai_client.get_api_base_url()
                client_metadata["model"] = ai_client.get_current_model()
                return (analyzer or self._analyzer)(
                    ai_client, prompt, original_data)
            finally:
                self._ai_client_manager.release_client(ai_client)

        retryer = Retrying(
            wait=self._retry_wait,
            stop=stop_after_attempt(3),
            retry=(retry_if_exception_type(Exception)
                   | retry_if_result(self._is_retryable_analysis_result)),
            retry_error_callback=lambda state: state.outcome.result(),
            reraise=True,
        )
        result = retryer(analyze_once)
        return result, prompt_version, client_metadata["service"], client_metadata["model"]

    @staticmethod
    def _transient_prompt_options(original_data: dict) -> tuple[int | None, str | None]:
        settings = original_data.get("temp_data", {}).get("manual_debug", {})
        version = settings.get("prompt_version")
        version = int(version) if version not in (None, "") else None
        override = original_data.get("prompt")
        if override is not None:
            override = str(override)
            if not override.strip():
                raise ValueError("prompt_override 不能为空")
        return version, override

    def _wait_for_ai_client(self, ctx: Any, user_name: str) -> Any:
        attempts = 0
        while not self._stop_event.is_set():
            kwargs = {}
            if getattr(ctx, "ai_client_group", None):
                kwargs["target_group_id"] = ctx.ai_client_group
            client = self._ai_client_manager.get_available_client(user_name, **kwargs)
            if client is not None:
                return client
            attempts += 1
            if attempts % 10 == 0:
                logger.warning("Hub analysis is waiting for an available AI client (%ss).", attempts)
            self._stop_event.wait(self._client_wait_interval)
        raise RuntimeError("hub_stopping_while_waiting_for_ai_client")

    @staticmethod
    def _is_retryable_analysis_result(result: Any) -> bool:
        if not isinstance(result, dict):
            return True
        return bool(result.get("error")) and result.get("api_error_code") != "HTTP_400"

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
        if not uuid or not ctx.mongo_db_cache:
            return
        try:
            ctx.mongo_db_cache.update(
                {"UUID": uuid}, {f"APPENDIX.{APPENDIX_ARCHIVED_FLAG}": state})
        except Exception:
            logger.exception("Failed to mark cache record %s as %s.", uuid, state)

    @staticmethod
    def _increment_stat(ctx: Any, name: str) -> None:
        stats = getattr(ctx, "stats", None)
        if isinstance(stats, dict):
            stats[name] = stats.get(name, 0) + 1

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
