"""Event V4 主链适配器；与旧 IIS 新闻式流程保持独立。"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from uuid import UUID, uuid5

from pydantic import ValidationError
from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_TABLE

from ServiceComponent.IntelligenceHubDefines_v4 import (
    CollectedDataV4,
    DEFAULT_EVENT_REGISTRY,
    NonIntelligenceV4,
    ValuableIntelligenceV4,
    validate_analysis_result_v4,
)
from ServiceComponent.adapters.base_pipeline import BasePipelinePorts
from ServiceComponent.event_v4_archive import (
    ArchivedIntelligenceV4,
    EventV4ArchiveRepository,
    LowValueIntelligenceV4,
)
from ServiceComponent.event_v4_conversion import (
    DeterministicEntityResolver,
    EntityResolver,
    EventConversionResult,
    convert_event_extraction,
)
from ServiceComponent.manual_debug_analysis import MANUAL_TEST_SOURCE
from ServiceComponent.pipeline.contracts import StageResult
from ServiceComponent.runtime.events import HubEvent


INTELLIGENCE_NAMESPACE = UUID("55672c83-cdd8-5754-a724-8a2ae8874fb7")
ARCHIVED_FLAG_DROP = "D"
ARCHIVED_FLAG_ERROR = "E"
ARCHIVED_FLAG_ARCHIVED = "A"
ARCHIVED_FLAG_SENSITIVE = "S"
ARCHIVED_FLAG_DUPLICATED = "U"
CACHE_ARCHIVED_FLAG_FIELD = "APPENDIX.__ARCHIVED__"


@dataclass(frozen=True, slots=True)
class PreparedValuableV4:
    archive: ArchivedIntelligenceV4
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
        entity_resolver: EntityResolver | None = None,
        event_registry: Any = DEFAULT_EVENT_REGISTRY,
        prompt_table: Mapping[int, str] | None = None,
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
        self._entity_resolver = entity_resolver
        self._event_registry = event_registry
        self._failure_recorder = failure_recorder
        self._prompt_table = dict(prompt_table or EVENT_ANALYSIS_PROMPT_TABLE)
        if not self._prompt_table:
            raise ValueError("Event V4 prompt_table 不能为空")

    def accept(self, event: HubEvent) -> StageResult:
        ctx = self._resolve_context(event)
        try:
            data = CollectedDataV4.model_validate(dict(event.payload)).model_dump()
        except ValidationError as exc:
            return StageResult.reject(str(exc))
        if data.get("source") == MANUAL_TEST_SOURCE:
            return StageResult.reject("manual_test_requires_debug_route")
        data["subsystem"] = ctx.name
        with self._dedupe_lock:
            collection = ctx.mongo_db_cache.collection if ctx.mongo_db_cache else None
            if collection is not None and collection.find_one({"$or": [
                {"UUID": data["UUID"]}, {"informant": data["informant"]},
            ]}, {"_id": 1}):
                return StageResult.reject("collected_data_duplicated")
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
        return dict(self._prompt_table)

    def _analyze_v4(self, event: HubEvent, *, persist: bool) -> StageResult:
        ctx = self._resolve_context(event)
        original_data = dict(event.payload)
        intelligence_uuid = derive_intelligence_uuid(original_data)
        informant = str(original_data.get("informant", "")).strip()
        repository = self._require_archive_repository(ctx) if persist else None
        if persist and repository.contains(intelligence_uuid, informant):
            self._mark_cache(original_data.get("UUID", ""), ARCHIVED_FLAG_DUPLICATED, ctx)
            self._increment_stat(ctx, "dropped")
            return StageResult.reject("already_archived")
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
            if isinstance(analysis, NonIntelligenceV4):
                low_value = LowValueIntelligenceV4(
                    intelligence_uuid=intelligence_uuid,
                    informant=informant,
                    raw_data=original_data,
                    analysis=analysis,
                    ai_service=ai_service,
                    ai_model=ai_model,
                    prompt_version=prompt_version,
                    processed_at=processed_at,
                    subsystem=ctx.name,
                )
                if persist:
                    repository.save_low_value(low_value)
                    self._mark_cache(original_data.get("UUID", ""), ARCHIVED_FLAG_DROP, ctx)
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
                analysis.event_extraction,
                intelligence_uuid=intelligence_uuid,
                entity_resolver=(self._resolve_entity_resolver(ctx) if persist
                                 else DeterministicEntityResolver()),
                registry=self._event_registry,
                observed_at=processed_at,
                metadata={"informant": informant, "subsystem": ctx.name},
            )
            scorer = self._scorer_factory(getattr(ctx, "scoring_config", None))
            if not hasattr(scorer, "calculate_v4"):
                raise TypeError("V4 评分器必须实现 calculate_v4()")
            total_score = scorer.calculate_v4(analysis)
            archive = ArchivedIntelligenceV4(
                intelligence_uuid=intelligence_uuid,
                informant=informant,
                raw_data=original_data,
                analysis=analysis,
                ai_service=ai_service,
                ai_model=ai_model,
                prompt_version=prompt_version,
                processed_at=processed_at,
                archived_at=processed_at,
                total_score=total_score,
                event_uuids=[item.uuid for item in conversion.events],
                primary_event_uuid=conversion.primary_event_uuid,
                subsystem=ctx.name,
            )
            if not persist:
                from event_engine.query.serialization import event_to_document

                result = archive.model_dump(mode="json", by_alias=True)
                result["events"] = [event_to_document(item) for item in conversion.events]
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
                self._mark_cache(original_data.get("UUID", ""), ARCHIVED_FLAG_SENSITIVE, ctx)
                self._increment_stat(ctx, "error")
            return StageResult.reject(f"analysis_failed:{exc}")
        except Exception as exc:
            self._change_runtime_stat("failed")
            if persist:
                self._mark_cache(original_data.get("UUID", ""), ARCHIVED_FLAG_ERROR, ctx)
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
            archive = prepared.archive.model_copy(
                update={"archived_at": datetime.now(timezone.utc)}
            )
            repository.commit(archive, prepared.conversion.events)
            source_uuid = str(archive.raw_data.get("UUID", ""))
            self._mark_cache(source_uuid, ARCHIVED_FLAG_ARCHIVED, ctx)
            self._increment_stat(ctx, "archived")
            return StageResult.allow(
                archive.model_dump(mode="json", by_alias=True),
                subsystem=ctx.name,
            )
        except Exception as exc:
            source_uuid = str(prepared.archive.raw_data.get("UUID", ""))
            self._mark_cache(source_uuid, ARCHIVED_FLAG_ERROR, ctx)
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

    def _resolve_entity_resolver(self, ctx: Any) -> EntityResolver:
        return (
            self._entity_resolver
            or getattr(ctx, "event_v4_entity_resolver", None)
            or DeterministicEntityResolver()
        )

    @staticmethod
    def _mark_cache(uuid: str, state: str, ctx: Any) -> None:
        if not uuid or not ctx.mongo_db_cache:
            return
        ctx.mongo_db_cache.update({"UUID": uuid}, {CACHE_ARCHIVED_FLAG_FIELD: state})

    def _analyze_v4_with_retry(
        self, ctx: Any, event: HubEvent, original_data: dict, *, analyzer=None,
        prompt_version: int | None = None, prompt_override: str | None = None,
    ) -> tuple[ValuableIntelligenceV4 | NonIntelligenceV4, int, str, str]:
        if prompt_version is None:
            prompt_version = random.choice(list(self._prompt_table.keys()))
        elif prompt_version not in self._prompt_table:
            raise ValueError(f"prompt_version_not_configured:{prompt_version}")
        base_prompt = (
            prompt_override if prompt_override is not None
            else self._prompt_table[prompt_version]
        )
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
                    validated = validate_analysis_result_v4(raw_result)
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


def derive_intelligence_uuid(data: dict[str, Any]) -> UUID:
    """优先沿用合法 UUID，否则以原始标识和来源稳定派生全局 UUID。"""

    raw_uuid = str(data.get("UUID", "")).strip()
    try:
        return UUID(raw_uuid)
    except (ValueError, TypeError, AttributeError):
        source = str(data.get("informant", "")).strip()
        return uuid5(INTELLIGENCE_NAMESPACE, f"{raw_uuid}|{source}")


def _compact_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        payload = exc.errors(include_url=False, include_input=False)
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(exc)
    return text.replace("\n", " ")[:800]
