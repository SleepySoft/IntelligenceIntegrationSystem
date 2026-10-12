"""Event V4 主链适配器；与旧 IIS 新闻式流程保持独立。"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from pydantic import ValidationError
from event_engine.extraction import complete_core_extraction
from prompts_event_v4 import (
    EVENT_ANALYSIS_EXTRA_PROMPTS,
    EVENT_ANALYSIS_PROMPT_TABLE,
    EXTRACTION_MODE_CORE_SUBSET,
    EXTRACTION_MODE_FULL,
    PROMPT_EXTRACTION_MODES,
)

from ServiceComponent.IntelligenceHubDefines_v4 import (
    AnalysisResultV4,
    CollectedData,
    DEFAULT_EVENT_REGISTRY,
    validate_analysis_result_v4,
)
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
from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME,
    APPENDIX_CONTENT_TIME_SOURCE,
    APPENDIX_RAW_DATA,
    APPENDIX_TIME_ARCHIVED,
    CACHE_ARCHIVED_FLAG_FIELD,
    CacheArchiveState,
)
from ServiceComponent.intelligence_time import resolve_content_time
from ServiceComponent.intelligence_identity import normalize_or_assign_intelligence_uuid
from ServiceComponent.pipeline.contracts import StageResult
from ServiceComponent.runtime.events import HubEvent


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
        self._prompt_table = dict(prompt_table or EVENT_ANALYSIS_PROMPT_TABLE)
        if not self._prompt_table:
            raise ValueError("Event V4 prompt_table 不能为空")
        # 额外版本仅供显式选择（人工调试/灰度实验），不参与生产抽样；
        # 与抽样表重叠时以抽样表为准。
        extra = dict(EVENT_ANALYSIS_EXTRA_PROMPTS if extra_prompt_versions is None
                     else extra_prompt_versions)
        self._selectable_prompts = {**extra, **self._prompt_table}
        self._extraction_modes = dict(PROMPT_EXTRACTION_MODES if extraction_modes is None
                                      else extraction_modes)

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
            prompt_version = random.choice(list(self._prompt_table.keys()))
        elif prompt_version not in self._selectable_prompts:
            raise ValueError(f"prompt_version_not_configured:{prompt_version}")
        base_prompt = (
            prompt_override if prompt_override is not None
            else self._selectable_prompts[prompt_version]
        )
        # 协议模式跟随所选版本；prompt_override 通常是对该版本的改写，沿用同一模式。
        extraction_mode = self._extraction_modes.get(prompt_version, EXTRACTION_MODE_FULL)
        prompt = base_prompt
        user_name = f"HubRuntime-v4-{event.correlation_id or event.event_id}"
        service = ""
        model = ""
        last_error: Exception | None = None
        validation_feedback: list[str] = []

        for attempt in range(1, 4):
            ai_client = self._wait_for_ai_client(ctx, user_name)
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
            elif raw_result is not None:
                self._change_runtime_stat("validation_running", 1)
                try:
                    validated = self._validate_analysis(raw_result, extraction_mode)
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

    def _validate_analysis(self, raw_result: dict, extraction_mode: str) -> AnalysisResultV4:
        """按 Prompt 版本的抽取协议模式校验分析结果。

        核心子集模式下先把 EVENT_EXTRACTION 确定性补齐为完整的
        event-extraction/1.0 结果，再走统一的 AnalysisResultV4 校验；
        归档与下游索引消费的仍是完整契约。
        """
        if extraction_mode != EXTRACTION_MODE_CORE_SUBSET or not isinstance(raw_result, dict):
            return validate_analysis_result_v4(raw_result)
        data = raw_result
        extraction = raw_result.get("EVENT_EXTRACTION")
        if extraction is not None:
            completed = complete_core_extraction(extraction, self._event_registry)
            data = dict(raw_result)
            data["EVENT_EXTRACTION"] = completed.model_dump(mode="python")
        return validate_analysis_result_v4(data)

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
