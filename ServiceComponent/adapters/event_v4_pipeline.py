"""Event V4 主链适配器；与旧 IIS 新闻式流程保持独立。"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from uuid import UUID, uuid5

from pydantic import ValidationError
from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_TABLE

from ServiceComponent.IntelligenceHubDefines_v2 import (
    ARCHIVED_FLAG_ARCHIVED,
    ARCHIVED_FLAG_DROP,
    ARCHIVED_FLAG_DUPLICATED,
    ARCHIVED_FLAG_ERROR,
    ARCHIVED_FLAG_SENSITIVE,
)
from ServiceComponent.IntelligenceHubDefines_v4 import (
    DEFAULT_EVENT_REGISTRY,
    NonIntelligenceV4,
    ValuableIntelligenceV4,
    validate_analysis_result_v4,
)
from ServiceComponent.adapters.iis_pipeline import IISPipelinePorts
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


@dataclass(frozen=True, slots=True)
class PreparedValuableV4:
    archive: ArchivedIntelligenceV4
    conversion: EventConversionResult


class EventV4PipelinePorts(IISPipelinePorts):
    """Event V4 的 intake、analysis、archive 三端口实现。

    仅复用旧适配器中与版本无关的 intake、客户端等待、缓存状态和统计辅助逻辑；
    分析结果、评分、事件转换和归档均使用独立的 V4 契约。
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
        client_wait_interval: float = 1.0,
    ):
        super().__init__(
            subsystem_registry,
            ai_client_manager,
            analyzer=analyzer,
            transient_analyzer=transient_analyzer,
            scorer_factory=scorer_factory,
            retry_wait=retry_wait,
            client_wait_interval=client_wait_interval,
        )
        self._archive_repository = archive_repository
        self._entity_resolver = entity_resolver or DeterministicEntityResolver()
        self._event_registry = event_registry
        self._prompt_table = dict(prompt_table or EVENT_ANALYSIS_PROMPT_TABLE)
        if not self._prompt_table:
            raise ValueError("Event V4 prompt_table 不能为空")

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
        repository = self._require_archive_repository() if persist else None
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
                entity_resolver=(self._entity_resolver if persist
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
            if persist:
                self._mark_cache(original_data.get("UUID", ""), ARCHIVED_FLAG_SENSITIVE, ctx)
                self._increment_stat(ctx, "error")
            return StageResult.reject(f"analysis_failed:{exc}")
        except Exception as exc:
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
            repository = self._require_archive_repository()
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

    def _require_archive_repository(self) -> EventV4ArchiveRepository:
        if self._archive_repository is None:
            raise RuntimeError("Event V4 端口仅配置为无持久化调试模式")
        return self._archive_repository

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

        for attempt in range(1, 4):
            ai_client = self._wait_for_ai_client(ctx, user_name)
            try:
                service = ai_client.get_api_base_url()
                model = ai_client.get_current_model()
                raw_result = (analyzer or self._analyzer)(ai_client, prompt, original_data)
            except Exception as exc:
                last_error = exc
                raw_result = None
            finally:
                self._ai_client_manager.release_client(ai_client)

            if isinstance(raw_result, dict) and raw_result.get("error"):
                if raw_result.get("api_error_code") == "HTTP_400":
                    raise _SensitiveAnalysisError(str(raw_result["error"]))
                last_error = RuntimeError(str(raw_result["error"]))
            elif raw_result is not None:
                try:
                    return (
                        validate_analysis_result_v4(raw_result),
                        prompt_version,
                        service,
                        model,
                    )
                except (ValidationError, ValueError, TypeError) as exc:
                    last_error = exc
                    feedback = _compact_validation_error(exc)
                    prompt = (
                        base_prompt
                        + "\n\n# 上一次输出的修正要求\n"
                        + "上一次 JSON 未通过服务端校验。请重新生成完整 JSON，不要解释。错误："
                        + feedback
                    )
            if attempt == 3:
                break
            if callable(self._retry_wait):
                delay = float(self._retry_wait(self._retry_state(attempt)) or 0)
                if delay > 0:
                    self._stop_event.wait(delay)
        assert last_error is not None
        raise last_error

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
