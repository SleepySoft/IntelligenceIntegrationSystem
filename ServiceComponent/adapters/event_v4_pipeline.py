"""Event V4 主链适配器；与旧 IIS 新闻式流程保持独立。"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from pydantic import ValidationError
from AIClientCenter.core.rate_limit import estimate_message_tokens
from prompts_event_v4 import (
    EVENT_ANALYSIS_EXTRA_PROMPTS,
    EVENT_ANALYSIS_PROMPT_TABLE,
    EXTRACTION_MODE_FULL,
    PROMPT_EXTRACTION_MODES,
    build_event_v4_prompt_catalog,
    build_event_v4_protocols,
)

from ServiceComponent.IntelligenceHubDefines_v4 import (
    AnalysisResultV4,
    CollectedData,
    DEFAULT_EVENT_REGISTRY,
    validate_analysis_result_v4,
)
from ServiceComponent.analysis_messages import (
    ANALYSIS_MAX_OUTPUT_TOKENS,
    build_analyze_message,
)
from ServiceComponent.prompt_protocols import PromptCatalog
from ServiceComponent.adapters.base_pipeline import BasePipelinePorts
from ServiceComponent.event_v4_archive import (
    ArchivedDataV4,
    EventV4ArchiveRepository,
    build_archive_appendix,
)
from ServiceComponent.event_v4_conversion import (
    EventConversionResult,
    convert_event_extraction,
)
from ServiceComponent.manual_debug_analysis import MANUAL_TEST_SOURCE
from ServiceComponent.extraction_quality import compute_extraction_quality
from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME,
    APPENDIX_CONTENT_TIME_SOURCE,
    APPENDIX_EXTRACTION_QUALITY,
    APPENDIX_RAW_DATA,
    APPENDIX_TIME_ARCHIVED,
    CACHE_ARCHIVED_FLAG_FIELD,
    CacheArchiveState,
)
from ServiceComponent.intelligence_time import resolve_content_time
from ServiceComponent.intelligence_identity import normalize_or_assign_intelligence_uuid
from ServiceComponent.pipeline.contracts import StageResult
from ServiceComponent.runtime.events import HubEvent


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedValuableV4:
    archive: ArchivedDataV4
    conversion: EventConversionResult


class EventV4PipelinePorts(BasePipelinePorts):
    """Event V4 的 intake、analysis、archive 三端口实现。

    分析结果、评分、事件转换和归档全部使用 V4 契约。
    """

    def __init__(
        self,
        subsystem_registry: Any,
        ai_client_manager: Any,
        *,
        archive_repository: EventV4ArchiveRepository | None = None,
        event_registry: Any = DEFAULT_EVENT_REGISTRY,
        prompt_catalog: PromptCatalog | None = None,
        prompt_table: Mapping[int, str] | None = None,
        extra_prompt_versions: Mapping[int, str] | None = None,
        extraction_modes: Mapping[int, str] | None = None,
        analyzer: Callable[[Any, str, dict], dict] | None = None,
        transient_analyzer: Callable[[Any, str, dict], dict] | None = None,
        scorer_factory: Callable[[dict | None], Any] | None = None,
        retry_wait: Any = None,
        client_wait_timeout: float = 60.0,
        analysis_worker_count: int = 1,
        failure_recorder: Any = None,
        shadow_versions: Iterable[int] | None = None,
        shadow_recorder: Any = None,
        quality_enabled: bool = True,
    ):
        super().__init__(
            subsystem_registry,
            ai_client_manager,
            analyzer=analyzer,
            transient_analyzer=transient_analyzer,
            scorer_factory=scorer_factory,
            retry_wait=retry_wait,
            client_wait_timeout=client_wait_timeout,
            analysis_worker_count=analysis_worker_count,
        )
        self._archive_repository = archive_repository
        self._event_registry = event_registry
        self._failure_recorder = failure_recorder
        self._prompt_catalog = prompt_catalog or self._build_catalog(
            event_registry, prompt_table, extra_prompt_versions, extraction_modes)
        self._prompt_table = self._prompt_catalog.sampling_table()
        if not self._prompt_table:
            raise ValueError("Event V4 prompt_catalog 没有参与生产抽样的 prompt 版本")
        self._selectable_prompts = self._prompt_catalog.selectable_table()
        # 影子链路：与主链并行执行同构分析，结果只落盘对比，不归档、不影响主链。
        self._shadow_versions = [int(version) for version in (shadow_versions or ())]
        unknown_shadow = [v for v in self._shadow_versions if v not in self._selectable_prompts]
        if unknown_shadow:
            raise ValueError(f"shadow prompt versions not configured: {unknown_shadow}")
        self._shadow_recorder = shadow_recorder
        self._quality_enabled = quality_enabled
        self._shadow_stats_lock = threading.Lock()
        self._shadow_stats = {"recorded": 0, "failed": 0}

    @staticmethod
    def _build_catalog(event_registry: Any,
                       prompt_table: Mapping[int, str] | None,
                       extra_prompt_versions: Mapping[int, str] | None,
                       extraction_modes: Mapping[int, str] | None) -> PromptCatalog:
        """未显式传入目录时：默认使用 Event V4 目录；传入旧式表参数时折算为目录。"""
        if prompt_table is None and extra_prompt_versions is None and extraction_modes is None:
            return build_event_v4_prompt_catalog(event_registry)
        catalog = PromptCatalog()
        for protocol in build_event_v4_protocols(event_registry):
            catalog.register_protocol(protocol)
        table = dict(prompt_table or EVENT_ANALYSIS_PROMPT_TABLE)
        extra = dict(EVENT_ANALYSIS_EXTRA_PROMPTS if extra_prompt_versions is None
                     else extra_prompt_versions)
        modes = dict(PROMPT_EXTRACTION_MODES if extraction_modes is None
                     else extraction_modes)
        for version, text in {**extra, **table}.items():
            catalog.register_prompt(
                int(version), text,
                protocol=modes.get(int(version), EXTRACTION_MODE_FULL),
                sampling=int(version) in table,
            )
        return catalog

    def accept(self, event: HubEvent) -> StageResult:
        ctx = self._resolve_context(event)
        try:
            submitted = dict(event.payload)
            submitted["UUID"] = str(normalize_or_assign_intelligence_uuid(
                submitted.get("UUID")))
            data = CollectedData.model_validate(submitted).model_dump()
        except ValidationError as exc:
            return StageResult.reject(str(exc))
        if data.get("source") == MANUAL_TEST_SOURCE:
            return StageResult.reject("manual_test_requires_debug_route")
        data["subsystem"] = ctx.name
        with self._dedupe_lock:
            collection = ctx.mongo_db_cache.collection if ctx.mongo_db_cache else None
            if collection is not None:
                if collection.find_one({"informant": data["informant"]}, {"_id": 1}):
                    return StageResult.reject("collected_data_duplicated")
                while collection.find_one({"UUID": data["UUID"]}, {"_id": 1}):
                    data["UUID"] = str(normalize_or_assign_intelligence_uuid(None))
            if ctx.mongo_db_cache:
                ctx.mongo_db_cache.insert(data)
        return StageResult.allow(data, subsystem=ctx.name)

    def analyze(self, event: HubEvent) -> StageResult:
        return self._analyze_v4(event, persist=True)

    def analyze_transient(self, event: HubEvent) -> StageResult:
        if dict(event.payload).get("source") != MANUAL_TEST_SOURCE:
            return StageResult.reject("transient_analysis_requires_manual_test_source")
        return self._analyze_v4(event, persist=False)

    def get_transient_prompt_table(self, subsystem: str) -> dict[int, str]:
        if self._registry.resolve(subsystem) is None:
            raise ValueError(f"unknown_subsystem:{subsystem!r}")
        return dict(self._selectable_prompts)

    def _analyze_v4(self, event: HubEvent, *, persist: bool) -> StageResult:
        ctx = self._resolve_context(event)
        original_data = dict(event.payload)
        informant = str(original_data.get("informant", "")).strip()
        if not informant:
            return StageResult.reject("informant_required")
        original_uuid = str(original_data.get("UUID", "")).strip()
        intelligence_uuid = normalize_or_assign_intelligence_uuid(original_uuid)
        canonical_uuid = str(intelligence_uuid)
        if canonical_uuid != original_uuid:
            original_data["UUID"] = canonical_uuid
            if persist and ctx.mongo_db_cache:
                cache_identity = ({"UUID": original_uuid} if original_uuid
                                  else {"informant": informant})
                ctx.mongo_db_cache.update(cache_identity, {"UUID": canonical_uuid})
        repository = self._require_archive_repository(ctx) if persist else None
        if persist and repository.contains_source(informant):
            self._mark_cache(original_data.get("UUID", ""), CacheArchiveState.DUPLICATED, ctx)
            self._increment_stat(ctx, "dropped")
            return StageResult.reject("already_archived")
        if persist:
            while repository.contains_id(intelligence_uuid):
                previous_uuid = original_data["UUID"]
                intelligence_uuid = normalize_or_assign_intelligence_uuid(None)
                original_data["UUID"] = str(intelligence_uuid)
                if ctx.mongo_db_cache:
                    ctx.mongo_db_cache.update(
                        {"UUID": previous_uuid}, {"UUID": original_data["UUID"]})
        try:
            requested_prompt_version, prompt_override = (
                self._transient_prompt_options(original_data) if not persist else (None, None)
            )
            analysis, prompt_version, ai_service, ai_model = self._analyze_v4_with_retry(
                ctx, event, original_data,
                analyzer=self._analyzer if persist else self._transient_analyzer,
                prompt_version=requested_prompt_version,
                prompt_override=prompt_override,
            )
            self._run_shadow_analysis(
                ctx, event, original_data,
                primary_version=prompt_version, persist=persist,
            )
            processed_at = datetime.now(timezone.utc)
            if analysis.is_non_intelligence:
                appendix = build_archive_appendix(
                    original_data,
                    subsystem=ctx.name,
                    ai_service=ai_service,
                    ai_model=ai_model,
                    prompt_version=prompt_version,
                    processed_at=processed_at,
                    archived_at=processed_at,
                )
                low_value = ArchivedDataV4(
                    **analysis.model_dump(mode="python", by_alias=True),
                    UUID=str(intelligence_uuid),
                    INFORMANT=informant,
                    APPENDIX=appendix,
                )
                if persist:
                    repository.save_low_value(low_value)
                    self._mark_cache(original_data.get("UUID", ""), CacheArchiveState.DROP, ctx)
                    self._increment_stat(ctx, "dropped")
                    return StageResult.reject("non_intelligence")
                debug_result = low_value.model_dump(mode="json", by_alias=True)
                debug_result["debug_prompt"] = {
                    "version": prompt_version,
                    "overridden": bool(prompt_override),
                }
                return StageResult.allow(
                    debug_result,
                    subsystem=ctx.name, transient=True, low_value=True,
                )

            conversion = convert_event_extraction(
                analysis.EVENT_EXTRACTION,
                intelligence_uuid=intelligence_uuid,
                registry=self._event_registry,
                observed_at=processed_at,
                metadata={"informant": informant, "subsystem": ctx.name},
            )
            scorer = self._scorer_factory(getattr(ctx, "scoring_config", None))
            if not hasattr(scorer, "calculate_v4"):
                raise TypeError("V4 评分器必须实现 calculate_v4()")
            total_score = scorer.calculate_v4(analysis)
            appendix = build_archive_appendix(
                original_data,
                subsystem=ctx.name,
                ai_service=ai_service,
                ai_model=ai_model,
                prompt_version=prompt_version,
                processed_at=processed_at,
                archived_at=processed_at,
                total_score=total_score,
            )
            quality = self._compute_quality(analysis, original_data)
            if quality is not None:
                appendix[APPENDIX_EXTRACTION_QUALITY] = quality
            archive = ArchivedDataV4(
                **analysis.model_dump(mode="python", by_alias=True),
                UUID=str(intelligence_uuid),
                INFORMANT=informant,
                APPENDIX=appendix,
            )
            if not persist:
                result = archive.model_dump(mode="json", by_alias=True)
                # conversion.events 已经是可查询的派生事件文档，不再是 Event Engine
                # 的领域对象，也不应再经过旧的 UUID 引用序列化器。
                result["events"] = [dict(item) for item in conversion.events]
                result["debug_prompt"] = {
                    "version": prompt_version,
                    "overridden": bool(prompt_override),
                }
                return StageResult.allow(
                    result, subsystem=ctx.name, transient=True, low_value=False,
                )
            return StageResult.allow(
                PreparedValuableV4(archive=archive, conversion=conversion),
                subsystem=ctx.name,
            )
        except _SensitiveAnalysisError as exc:
            self._change_runtime_stat("failed")
            if persist:
                self._mark_cache(original_data.get("UUID", ""), CacheArchiveState.SENSITIVE, ctx)
                self._increment_stat(ctx, "error")
            return StageResult.reject(f"analysis_failed:{exc}")
        except Exception as exc:
            self._change_runtime_stat("failed")
            if persist:
                self._mark_cache(original_data.get("UUID", ""), CacheArchiveState.ERROR, ctx)
                self._increment_stat(ctx, "error")
            return StageResult.reject(f"analysis_exception:{type(exc).__name__}:{exc}")

    def archive(self, event: HubEvent) -> StageResult:
        ctx = self._resolve_context(event)
        prepared = event.payload
        if not isinstance(prepared, PreparedValuableV4):
            self._increment_stat(ctx, "error")
            return StageResult.reject("invalid_v4_archive_payload")
        try:
            repository = self._require_archive_repository(ctx)
            archive = prepared.archive.model_copy(deep=True)
            archived_at = datetime.now(timezone.utc)
            archive.APPENDIX[APPENDIX_TIME_ARCHIVED] = archived_at
            content_time, source = resolve_content_time(
                archive.APPENDIX.get(APPENDIX_RAW_DATA) or {}, archived_at)
            archive.APPENDIX[APPENDIX_CONTENT_TIME] = content_time
            archive.APPENDIX[APPENDIX_CONTENT_TIME_SOURCE] = source
            repository.commit(archive, prepared.conversion.events)
            source_uuid = archive.UUID
            self._mark_cache(source_uuid, CacheArchiveState.ARCHIVED, ctx)
            self._increment_stat(ctx, "archived")
            return StageResult.allow(
                archive.model_dump(mode="json", by_alias=True),
                subsystem=ctx.name,
            )
        except Exception as exc:
            source_uuid = prepared.archive.UUID
            self._mark_cache(source_uuid, CacheArchiveState.ERROR, ctx)
            self._increment_stat(ctx, "error")
            return StageResult.reject(f"archive_exception:{type(exc).__name__}:{exc}")

    def _require_archive_repository(self, ctx: Any) -> EventV4ArchiveRepository:
        repository = (
            self._archive_repository
            or getattr(ctx, "event_v4_archive_repository", None)
        )
        if repository is None:
            raise RuntimeError("Event V4 端口仅配置为无持久化调试模式")
        return repository

    @staticmethod
    def _mark_cache(uuid: str, state: CacheArchiveState, ctx: Any) -> None:
        if not uuid or not ctx.mongo_db_cache:
            return
        ctx.mongo_db_cache.update({"UUID": uuid}, {CACHE_ARCHIVED_FLAG_FIELD: state.value})

    def _analyze_v4_with_retry(
        self, ctx: Any, event: HubEvent, original_data: dict, *, analyzer=None,
        prompt_version: int | None = None, prompt_override: str | None = None,
    ) -> tuple[AnalysisResultV4, int, str, str]:
        if prompt_version is None:
            prompt_version = random.choice(self._prompt_catalog.sampling_versions())
        elif not self._prompt_catalog.has(prompt_version):
            raise ValueError(f"prompt_version_not_configured:{prompt_version}")
        protocol = self._prompt_catalog.protocol_for(prompt_version)
        base_prompt = (
            prompt_override if prompt_override is not None
            else self._prompt_catalog.text_for(prompt_version)
        )
        # 前处理/注入钩子属于协议；prompt_override 通常是对该版本的改写，沿用同一协议。
        base_prompt = protocol.prepare_prompt(base_prompt, original_data)
        prompt = base_prompt
        user_name = f"HubRuntime-v4-{event.correlation_id or event.event_id}"
        service = ""
        model = ""
        last_error: Exception | None = None
        validation_feedback: list[str] = []
        failed_client_names: set[str] = set()

        for attempt in range(1, 4):
            estimated_input_tokens = estimate_message_tokens(
                build_analyze_message(prompt, original_data)
            )
            ai_client = self._wait_for_ai_client(
                ctx,
                user_name,
                estimated_input_tokens=estimated_input_tokens,
                max_tokens=ANALYSIS_MAX_OUTPUT_TOKENS,
                avoid_client_names=set(failed_client_names),
            )
            self._change_runtime_stat("attempts")
            self._change_runtime_stat("ai_running", 1)
            call_started = time.perf_counter()
            try:
                service = ai_client.get_api_base_url()
                model = ai_client.get_current_model()
                raw_result = (analyzer or self._analyzer)(ai_client, prompt, original_data)
                self._change_runtime_stat("responses")
            except Exception as exc:
                self._change_runtime_stat("call_errors")
                self._record_failure_sample(
                    category="call", error=exc, raw_response=None,
                    original_data=original_data, ctx=ctx, event=event,
                    attempt=attempt, prompt_version=prompt_version, prompt=prompt,
                    ai_service=service, ai_model=model,
                )
                last_error = exc
                raw_result = None
                failed_client_names.add(getattr(ai_client, "name", ""))
            finally:
                call_ms = max(0, int((time.perf_counter() - call_started) * 1000))
                with self._runtime_stats_lock:
                    self._runtime_stats["last_call_ms"] = call_ms
                    self._runtime_stats["call_ms_total"] += call_ms
                self._change_runtime_stat("ai_running", -1)
                self._ai_client_manager.release_client(ai_client)

            if isinstance(raw_result, dict) and "record_file" in raw_result:
                # conversation_common_process 添加的本地诊断元数据不属于 Event V4 响应契约。
                raw_result = dict(raw_result)
                raw_result.pop("record_file", None)

            if isinstance(raw_result, dict) and raw_result.get("error"):
                self._change_runtime_stat("api_errors")
                api_error = RuntimeError(str(raw_result["error"]))
                self._record_failure_sample(
                    category="api", error=api_error, raw_response=raw_result,
                    original_data=original_data, ctx=ctx, event=event,
                    attempt=attempt, prompt_version=prompt_version, prompt=prompt,
                    ai_service=service, ai_model=model,
                )
                if raw_result.get("api_error_code") == "HTTP_400":
                    raise _SensitiveAnalysisError(str(raw_result["error"]))
                last_error = api_error
                failed_client_names.add(getattr(ai_client, "name", ""))
            elif raw_result is not None:
                self._change_runtime_stat("validation_running", 1)
                try:
                    validated = self._validate_analysis(raw_result, prompt_version)
                    self._change_runtime_stat("validated")
                    return (
                        validated,
                        prompt_version,
                        service,
                        model,
                    )
                except (ValidationError, ValueError, TypeError) as exc:
                    self._change_runtime_stat("validation_errors")
                    self._record_failure_sample(
                        category="validation", error=exc, raw_response=raw_result,
                        original_data=original_data, ctx=ctx, event=event,
                        attempt=attempt, prompt_version=prompt_version, prompt=prompt,
                        ai_service=service, ai_model=model,
                    )
                    last_error = exc
                    feedback = _compact_validation_error(exc)
                    validation_feedback.append(feedback)
                    prompt = (
                        base_prompt
                        + "\n\n# 上一次输出的修正要求\n"
                        + "此前 JSON 未通过服务端校验。请同时修正以下全部错误，"
                        + "重新生成完整 JSON，不要解释：\n"
                        + "\n".join(
                            f"{index}. {item}"
                            for index, item in enumerate(validation_feedback, start=1)
                        )
                    )
                finally:
                    self._change_runtime_stat("validation_running", -1)
            if attempt == 3:
                break
            self._change_runtime_stat("retries")
            if callable(self._retry_wait):
                delay = float(self._retry_wait(self._retry_state(attempt)) or 0)
                if delay > 0:
                    self._stop_event.wait(delay)
        assert last_error is not None
        raise last_error

    @property
    def shadow_statistics(self) -> dict[str, int]:
        with self._shadow_stats_lock:
            return dict(self._shadow_stats)

    def _compute_quality(self, analysis: AnalysisResultV4, original_data: dict) -> dict | None:
        """计算抽取质量统计；仅供后续聚合参考，任何异常都不得影响主链。"""
        if not self._quality_enabled or analysis.EVENT_EXTRACTION is None:
            return None
        raw_text = f"{original_data.get('title') or ''}\n{original_data.get('content') or ''}"
        try:
            return compute_extraction_quality(
                analysis.EVENT_EXTRACTION, self._event_registry, raw_text=raw_text)
        except Exception:
            logger.exception("extraction quality check failed; ignored")
            return None

    def _run_shadow_analysis(self, ctx: Any, event: HubEvent, original_data: dict, *,
                             primary_version: int, persist: bool) -> None:
        """主链分析成功后并行执行影子链路，结果仅落盘对比。任何失败都不影响主链。"""
        if not persist or self._shadow_recorder is None:
            return
        for version in self._shadow_versions:
            if version == primary_version:
                continue
            try:
                analysis, _, service, model = self._analyze_v4_with_retry(
                    ctx, event, original_data,
                    analyzer=self._analyzer,
                    prompt_version=version,
                )
                self._shadow_recorder.record(
                    original_data=original_data,
                    subsystem=ctx.name,
                    primary_prompt_version=primary_version,
                    shadow_prompt_version=version,
                    analysis=analysis.model_dump(mode="json", by_alias=True),
                    ai_service=service,
                    ai_model=model,
                    extraction_quality=self._compute_quality(analysis, original_data),
                )
                with self._shadow_stats_lock:
                    self._shadow_stats["recorded"] += 1
            except Exception:
                with self._shadow_stats_lock:
                    self._shadow_stats["failed"] += 1
                logger.warning("shadow analysis failed for prompt v%s", version, exc_info=True)

    def _validate_analysis(self, raw_result: dict, prompt_version: int) -> AnalysisResultV4:
        """按所选版本绑定的协议做后处理（桥接）与校验。

        归档与下游索引消费的始终是规范契约（AnalysisResultV4 + 完整
        event-extraction/1.0 的 EVENT_EXTRACTION）；各协议的差异封装在
        PromptProtocol.validate_result 内。
        """
        return self._prompt_catalog.protocol_for(prompt_version).validate_result(raw_result)

    def _record_failure_sample(
        self, *, category: str, error: Exception, raw_response: Any,
        original_data: dict, ctx: Any, event: HubEvent, attempt: int,
        prompt_version: int, prompt: str, ai_service: str, ai_model: str,
    ) -> None:
        if self._failure_recorder is None:
            return
        self._failure_recorder.record(
            category=category,
            error=error,
            raw_response=raw_response,
            original_data=original_data,
            subsystem=ctx.name,
            trace_id=event.correlation_id or event.event_id,
            attempt=attempt,
            prompt_version=prompt_version,
            prompt=prompt,
            ai_service=ai_service,
            ai_model=ai_model,
            transient=(original_data.get("source") == MANUAL_TEST_SOURCE),
        )

    @staticmethod
    def _retry_state(attempt: int):
        """兼容 tenacity wait strategy 所需的最小状态，不主动 sleep。"""

        class State:
            attempt_number = attempt
            seconds_since_start = 0
            outcome = None

        return State()


class _SensitiveAnalysisError(RuntimeError):
    pass

def _compact_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        payload = exc.errors(include_url=False, include_input=False)
        # Pydantic 的 ctx 可能携带原始 ValueError，诊断文本必须可序列化，
        # 否则首次校验失败会反过来中断重试。
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    else:
        text = str(exc)
    return text.replace("\n", " ")[:800]
