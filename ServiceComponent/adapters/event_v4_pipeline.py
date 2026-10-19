"""Event V4 主链适配器；与旧 IIS 新闻式流程保持独立。"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from ServiceComponent.analysis_errors import (
    AnalysisErrorClass,
    classify_analysis_error,
    retry_action_for,
    RETRY_SAME_MODEL,
)
from ServiceComponent.analysis_stats import (
    OUTCOME_RETRY_SCHEDULED,
    OUTCOME_SENSITIVE,
    OUTCOME_SUCCESS,
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
    APPENDIX_FIELD,
    APPENDIX_RAW_DATA,
    APPENDIX_RETRY_FIRST_FAILED_AT,
    APPENDIX_RETRY_LAST_ERROR,
    APPENDIX_RETRY_LEASE_UNTIL,
    APPENDIX_RETRY_LEVEL,
    APPENDIX_RETRY_NEXT_AT,
    APPENDIX_TIME_ARCHIVED,
    CACHE_ARCHIVED_FLAG_FIELD,
    CacheArchiveState,
    EventV4CollectionSuffix,
    appendix_path,
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
    retry_metadata: Mapping[str, Any]


class EventV4PipelinePorts(BasePipelinePorts):
    """Event V4 的 intake、analysis、archive 三端口实现。

    分析结果、评分、事件转换和归档全部使用 V4 契约。
    """

    # R1/R2/R3 分别在首次失败后的 1h、8h、48h 到期；R3 仍失败才进入 E。
    PERSISTENT_RETRY_OFFSETS = {
        1: timedelta(hours=1),
        2: timedelta(hours=8),
        3: timedelta(days=2),
    }

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
        shadow_sample_rate: float = 1.0,
        shadow_client_group: str | None = None,
        analysis_stats: Any = None,
        max_live_age_hours: float = 0.0,
        max_analysis_age_hours: float = 0.0,
        quality_enabled: bool = True,
        control_state: Any = None,
        entity_name_normalizer: Callable[[str], str | None] | None = None,
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
        # 影子采样率：主链成功样本按此概率进入影子链路，控制灰度 Token 成本。
        self._shadow_sample_rate = max(0.0, min(1.0, float(shadow_sample_rate)))
        # 影子链路定向 AI 客户端分组：固定后影子结果可与主链形成“同模型不同 prompt”
        # 或“同 prompt 不同模型”的干净对比；None = 由管理器随机分配。
        self._shadow_client_group = str(shadow_client_group or "").strip() or None
        # IIS 侧分析效果统计（持久化、client 外评估）；None = 关闭。
        self._analysis_stats = analysis_stats
        # 新鲜度阈值（小时）：超过该年龄的情报进 REPLAY 低优先级车道而非主队列，
        # 防止冷启动/久停后批量旧情报淹没问题。0 = 不启用。
        self._max_live_age_hours = max(0.0, float(max_live_age_hours))
        # 硬上限（小时）：超过即不再分析——intake 直接拒绝（cache 标 X 留痕），
        # 回放时批量弃用。0 = 不启用。
        self._max_analysis_age_hours = max(0.0, float(max_analysis_age_hours))
        self._quality_enabled = quality_enabled
        self._shadow_stats_lock = threading.Lock()
        self._shadow_stats = {"recorded": 0, "failed": 0}
        self._control_state = control_state
        # 可选后处理：EntityAlias 命中时把抽取实体名替换为系统标准名。
        # 未启用、未命中或解析异常均保留 LLM 原始名称，不能阻塞主链。
        self._entity_name_normalizer = entity_name_normalizer

    def set_entity_name_normalizer(
        self, normalizer: Callable[[str], str | None] | None,
    ) -> None:
        """由组合根注入/移除可选的实体标准名解析器。"""
        self._entity_name_normalizer = normalizer

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
            self._record_intake_rejection(ctx, event.payload, f"validation_error:{exc}")
            return StageResult.reject(str(exc))
        if data.get("source") == MANUAL_TEST_SOURCE:
            return StageResult.reject("manual_test_requires_debug_route")
        data["subsystem"] = ctx.name
        with self._dedupe_lock:
            collection = ctx.mongo_db_cache.collection if ctx.mongo_db_cache else None
            if collection is not None:
                if collection.find_one({"informant": data["informant"]}, {"_id": 1}):
                    self._record_intake_rejection(ctx, data, "collected_data_duplicated")
                    return StageResult.reject("collected_data_duplicated")
                while collection.find_one({"UUID": data["UUID"]}, {"_id": 1}):
                    data["UUID"] = str(normalize_or_assign_intelligence_uuid(None))
            if ctx.mongo_db_cache:
                ctx.mongo_db_cache.insert(data)
        # 按内容年龄分流：超旧直接弃用（不分析），超龄走低优先级车道，新鲜进主队列。
        age_hours = self._content_age_hours(data)
        if (self._max_analysis_age_hours > 0 and age_hours is not None
                and age_hours > self._max_analysis_age_hours):
            # cache 留痕（X）保证去重与审计；拒绝通知采集端标记 SKIPPED 不再重发。
            self._mark_cache(data["UUID"], CacheArchiveState.DEPRECATED, ctx)
            self._record_intake_rejection(
                ctx, data,
                f"stale_content: age {age_hours:.1f}h > {self._max_analysis_age_hours}h")
            return StageResult.reject("stale_content")
        if (self._max_live_age_hours > 0 and age_hours is not None
                and age_hours > self._max_live_age_hours):
            return StageResult.allow(data, subsystem=ctx.name, lane="replay")
        return StageResult.allow(data, subsystem=ctx.name)

    @staticmethod
    def _content_age_hours(data: Mapping[str, Any]) -> float | None:
        """情报年龄（小时）：优先 pub_time，其次 collect_time；无法判定返回 None（按新鲜处理）。"""
        value = data.get("pub_time") or data.get("collect_time")
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - value).total_seconds() / 3600

    @staticmethod
    def _record_intake_rejection(ctx: Any, data: Any, reason: str) -> None:
        """intake 拒绝审计：写入独立的 intake_rejected 集合（不进 cache 队列）。

        只读统计用途；任何失败都只记日志，绝不能反过来影响 intake 本身。
        """
        try:
            storage = getattr(ctx, "mongo_db_cache", None)
            if storage is None:
                return
            payload = dict(data) if isinstance(data, Mapping) else {}
            storage.db[ctx.collection_name(EventV4CollectionSuffix.INTAKE_REJECTED)].insert_one({
                "uuid": str(payload.get("UUID") or ""),
                "informant": str(payload.get("informant") or ""),
                "source": str(payload.get("source") or ""),
                "title": str(payload.get("title") or "")[:200],
                "subsystem": getattr(ctx, "name", ""),
                "reason": reason[:1000],
                "rejected_at": datetime.now(timezone.utc),
            })
        except Exception:
            logger.warning("intake rejection audit write failed", exc_info=True)

    def analyze(self, event: HubEvent) -> StageResult:
        self._wait_while_analysis_paused()
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
            analysis, prompt_version, ai_service, ai_model, primary_client = (
                self._analyze_v4_with_retry(
                    ctx, event, original_data,
                    analyzer=self._analyzer if persist else self._transient_analyzer,
                    prompt_version=requested_prompt_version,
                    prompt_override=prompt_override,
                )
            )
            self._run_shadow_analysis(
                ctx, event, original_data,
                primary_version=prompt_version, persist=persist,
                primary_client_name=primary_client, primary_model=ai_model,
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
                PreparedValuableV4(
                    archive=archive,
                    conversion=conversion,
                    retry_metadata=dict(original_data.get(APPENDIX_FIELD) or {}),
                ),
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
                retry_state = self._schedule_retry(
                    ctx, original_data.get("UUID", ""), original_data, exc)
                self._increment_stat(ctx, "error")
                return StageResult.reject(
                    f"analysis_{retry_state.lower()}:{type(exc).__name__}:{exc}")
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
            retry_state = self._schedule_retry(
                ctx, source_uuid, {APPENDIX_FIELD: prepared.retry_metadata}, exc)
            self._increment_stat(ctx, "error")
            return StageResult.reject(
                f"archive_{retry_state.lower()}:{type(exc).__name__}:{exc}")

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

    def _schedule_retry(
            self, ctx: Any, uuid: str, original_data: Mapping[str, Any], error: Exception,
    ) -> str:
        """把可恢复失败推进到 R1/R2/R3；R3 再失败后才标记 E。"""
        if not uuid or not ctx.mongo_db_cache:
            return CacheArchiveState.ERROR.value
        appendix = original_data.get(APPENDIX_FIELD) or {}
        try:
            current_level = int(appendix.get(APPENDIX_RETRY_LEVEL) or 0)
        except (TypeError, ValueError):
            current_level = 0
        now = datetime.now(timezone.utc)
        first_failed_at = appendix.get(APPENDIX_RETRY_FIRST_FAILED_AT)
        if not isinstance(first_failed_at, datetime):
            first_failed_at = now
        error_text = f"{type(error).__name__}: {error}"[:1000]

        if current_level >= 3:
            ctx.mongo_db_cache.update(
                {"UUID": uuid},
                {
                    "$set": {
                        CACHE_ARCHIVED_FLAG_FIELD: CacheArchiveState.ERROR.value,
                        appendix_path(APPENDIX_RETRY_LEVEL): 3,
                        appendix_path(APPENDIX_RETRY_LAST_ERROR): error_text,
                    },
                    "$unset": {
                        appendix_path(APPENDIX_RETRY_NEXT_AT): "",
                        appendix_path(APPENDIX_RETRY_LEASE_UNTIL): "",
                    },
                },
            )
            return CacheArchiveState.ERROR.value

        next_level = current_level + 1
        next_at = first_failed_at + self.PERSISTENT_RETRY_OFFSETS[next_level]
        ctx.mongo_db_cache.update(
            {"UUID": uuid},
            {
                "$set": {
                    CACHE_ARCHIVED_FLAG_FIELD: CacheArchiveState.RETRY.value,
                    appendix_path(APPENDIX_RETRY_LEVEL): next_level,
                    appendix_path(APPENDIX_RETRY_FIRST_FAILED_AT): first_failed_at,
                    appendix_path(APPENDIX_RETRY_NEXT_AT): next_at,
                    appendix_path(APPENDIX_RETRY_LAST_ERROR): error_text,
                },
                "$unset": {appendix_path(APPENDIX_RETRY_LEASE_UNTIL): ""},
            },
        )
        return f"R{next_level}"

    def _record_analysis_attempt(self, *, original_data: dict, ctx: Any,
                                 shadow: bool, prompt_version: int, service: str,
                                 model: str, attempt: int, latency_ms: int,
                                 error_class: str | None) -> None:
        """每次 AI 调用落一条统计；任何失败都不影响主链。"""
        recorder = self._analysis_stats
        if recorder is None:
            return
        try:
            recorder.record_attempt(
                uuid=str(original_data.get("UUID") or ""),
                subsystem=getattr(ctx, "name", ""), shadow=shadow,
                prompt_version=prompt_version, ai_service=service, ai_model=model,
                attempt_no=attempt, latency_ms=latency_ms, error_class=error_class)
        except Exception:
            pass

    def _record_analysis_outcome(self, *, original_data: dict, ctx: Any,
                                 shadow: bool, prompt_version: int, service: str,
                                 model: str, outcome: str, attempts_used: int) -> None:
        recorder = self._analysis_stats
        if recorder is None:
            return
        try:
            recorder.record_outcome(
                uuid=str(original_data.get("UUID") or ""),
                subsystem=getattr(ctx, "name", ""), shadow=shadow,
                prompt_version=prompt_version, ai_service=service, ai_model=model,
                outcome=outcome, attempts_used=attempts_used)
        except Exception:
            pass

    def _analyze_v4_with_retry(
        self, ctx: Any, event: HubEvent, original_data: dict, *, analyzer=None,
        prompt_version: int | None = None, prompt_override: str | None = None,
        client_group: str | None = None,
        avoid_client_names: set[str] | None = None,
        avoid_model: str | None = None,
        shadow: bool = False,
    ) -> tuple[AnalysisResultV4, int, str, str, str]:
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
        # avoid_client_names：跨分析避让（如影子链路避开主链所用客户端，保证模型不同）
        failed_client_names: set[str] = set(avoid_client_names or ())
        # 差异化重试：结构/语义校验错误优先同模型带反馈纠错一次，而非跨模型乱跳
        prefer_client_name: str | None = None
        same_model_retried = False
        # 敏感拒绝兑底：优先换 low_sensitivity 标签的客户端再试一次（有的话）
        sensitive_fallback_tried = False
        sensitive_tags: set[str] | None = None

        for attempt in range(1, 4):
            request_id = None
            attempt_error_class: str | None = None
            # prefer_client_name 只生效一次：用后立清，避免网络错误时锁死同一客户端
            prefer_now = prefer_client_name
            prefer_client_name = None
            tags_now = sensitive_tags if sensitive_tags else None
            sensitive_tags = None
            estimated_input_tokens = estimate_message_tokens(
                build_analyze_message(prompt, original_data)
            )
            ai_client = self._wait_for_ai_client(
                ctx,
                user_name,
                estimated_input_tokens=estimated_input_tokens,
                max_tokens=ANALYSIS_MAX_OUTPUT_TOKENS,
                avoid_client_names=set(failed_client_names),
                client_group=client_group,
                prefer_client_name=prefer_now,
                required_tags=tags_now,
            )
            # 模型硬避让（影子链路）：取到与避让模型相同的客户端时直接换，
            # 不发起调用——同模型的对比没有评估价值，不烧 Token。
            if avoid_model and ai_client.get_current_model() == avoid_model:
                failed_client_names.add(getattr(ai_client, "name", ""))
                self._ai_client_manager.release_client(ai_client)
                last_error = _SameModelError(
                    f"acquired model matches avoided model: {avoid_model}")
                if attempt == 3:
                    break
                continue
            self._change_runtime_stat("attempts")
            self._change_runtime_stat("ai_running", 1)
            call_started = time.perf_counter()
            try:
                service = ai_client.get_api_base_url()
                model = ai_client.get_current_model()
                raw_result = (analyzer or self._analyzer)(
                    ai_client, prompt, original_data,
                    record_meta={
                        "prompt_version": prompt_version,
                        "uuid": original_data.get("UUID"),
                    })
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

            if attempt_error_class is None and raw_result is None:
                attempt_error_class = classify_analysis_error("call", last_error)
            if isinstance(raw_result, dict) and raw_result.get("error"):
                attempt_error_class = classify_analysis_error(
                    "api", None, raw_result)
            if isinstance(raw_result, dict):
                client_meta = raw_result.pop("_ai_client_meta", None)
                if isinstance(client_meta, dict):
                    request_id = client_meta.get("request_id")
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
                if raw_result.get("api_error_code") == "CONTENT_SAFETY":
                    self._record_analysis_attempt(
                        original_data=original_data, ctx=ctx, shadow=shadow,
                        prompt_version=prompt_version, service=service, model=model,
                        attempt=attempt, latency_ms=call_ms,
                        error_class=AnalysisErrorClass.CONTENT_SAFETY)
                    # 敏感兑底：换一个带 low_sensitivity 标签的客户端再试一次（有的话）
                    if not sensitive_fallback_tried and attempt < 3:
                        sensitive_fallback_tried = True
                        sensitive_tags = {"low_sensitivity"}
                        failed_client_names.add(getattr(ai_client, "name", ""))
                        last_error = _SensitiveAnalysisError(str(raw_result["error"]))
                        continue
                    self._record_analysis_outcome(
                        original_data=original_data, ctx=ctx, shadow=shadow,
                        prompt_version=prompt_version, service=service, model=model,
                        outcome=OUTCOME_SENSITIVE, attempts_used=attempt)
                    raise _SensitiveAnalysisError(str(raw_result["error"]))
                last_error = api_error
                failed_client_names.add(getattr(ai_client, "name", ""))
            elif raw_result is not None:
                self._change_runtime_stat("validation_running", 1)
                try:
                    validated = self._validate_analysis(raw_result, prompt_version)
                    self._report_client_feedback(
                        ai_client, request_id, valid=True,
                        labels={"stage": "event_v4_validation", "attempt": attempt},
                    )
                    self._change_runtime_stat("validated")
                    self._record_analysis_attempt(
                        original_data=original_data, ctx=ctx, shadow=shadow,
                        prompt_version=prompt_version, service=service, model=model,
                        attempt=attempt, latency_ms=call_ms,
                        error_class=None)
                    self._record_analysis_outcome(
                        original_data=original_data, ctx=ctx, shadow=shadow,
                        prompt_version=prompt_version, service=service, model=model,
                        outcome=OUTCOME_SUCCESS, attempts_used=attempt)
                    return (
                        validated,
                        prompt_version,
                        service,
                        model,
                        getattr(ai_client, "name", ""),
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
                    # 差异化路由：结构/语义错误优先同客户端带反馈纠错一次，
                    # 而不是立刻跨模型乱跳；已试过一次同模型后回到避让策略。
                    attempt_error_class = classify_analysis_error("validation", exc)
                    if (retry_action_for(attempt_error_class) == RETRY_SAME_MODEL
                            and not same_model_retried and attempt < 3):
                        prefer_client_name = getattr(ai_client, "name", "") or None
                        same_model_retried = True
                    else:
                        prefer_client_name = None
                        failed_client_names.add(getattr(ai_client, "name", ""))
                    self._record_analysis_attempt(
                        original_data=original_data, ctx=ctx, shadow=shadow,
                        prompt_version=prompt_version, service=service, model=model,
                        attempt=attempt, latency_ms=call_ms,
                        error_class=attempt_error_class)
                    self._report_client_feedback(
                        ai_client, request_id, valid=False,
                        labels={
                            "stage": "event_v4_validation",
                            "attempt": attempt,
                            "error": _compact_validation_error(exc),
                            "error_class": attempt_error_class,
                        },
                    )
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
        self._record_analysis_outcome(
            original_data=original_data, ctx=ctx, shadow=shadow,
            prompt_version=prompt_version, service=service, model=model,
            outcome=OUTCOME_RETRY_SCHEDULED, attempts_used=3)
        raise last_error

    def _report_client_feedback(
            self, ai_client: Any, request_id: str | None, *,
            valid: bool, labels: dict[str, Any],
    ) -> None:
        reporter = getattr(self._ai_client_manager, "report_client_feedback", None)
        if callable(reporter):
            reporter(ai_client, request_id, valid=valid, labels=labels)

    def _wait_while_analysis_paused(self) -> None:
        """分析暂停时事件留在 worker 上等待恢复，不消费 AI、不标记错误。"""
        control = self._control_state
        if control is None:
            return
        while control.is_paused("analysis") and not self._stop_event.is_set():
            self._stop_event.wait(2.0)

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
                             primary_version: int, persist: bool,
                             primary_client_name: str = "",
                             primary_model: str = "") -> None:
        """主链分析成功后并行执行影子链路，结果仅落盘对比。任何失败都不影响主链。"""
        if not persist or self._shadow_recorder is None:
            return
        if self._control_state is not None and self._control_state.is_paused("shadow"):
            return
        # 抽样判定对整次事件做一次：命中时该事件的所有影子版本都执行，保证配对完整。
        if self._shadow_sample_rate < 1.0 and random.random() >= self._shadow_sample_rate:
            return
        for version in self._shadow_versions:
            # 未定向分组时跳过与主链同版本（同 prompt+随机模型≈重复劳动）；
            # 定向分组时同版本恰恰意味着“同 prompt 不同模型”，必须执行。
            if version == primary_version and not self._shadow_client_group:
                continue
            try:
                analysis, _, service, model, _ = self._analyze_v4_with_retry(
                    ctx, event, original_data,
                    analyzer=self._analyzer,
                    prompt_version=version,
                    client_group=self._shadow_client_group,
                    avoid_client_names={primary_client_name} if primary_client_name else None,
                    avoid_model=primary_model or None,
                    shadow=True,
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
        analysis = self._prompt_catalog.protocol_for(prompt_version).validate_result(raw_result)
        return self._normalize_entity_names(analysis)

    def _normalize_entity_names(self, analysis: AnalysisResultV4) -> AnalysisResultV4:
        """协议校验后的可选实体名规范化；任何失败均降级为保留原名。"""
        normalizer = self._entity_name_normalizer
        if normalizer is None or analysis.EVENT_EXTRACTION is None:
            return analysis
        normalized = analysis.model_copy(deep=True)
        try:
            for entity in normalized.EVENT_EXTRACTION.entities:
                original = entity.name
                try:
                    canonical = normalizer(original)
                except Exception:
                    logger.warning(
                        "entity name normalization failed for %r; original kept",
                        original,
                        exc_info=True,
                    )
                    continue
                canonical = str(canonical or "").strip()
                if canonical:
                    entity.name = canonical
        except Exception:
            logger.warning(
                "entity name normalization postprocess failed; original analysis kept",
                exc_info=True,
            )
            return analysis
        return normalized

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


class _SameModelError(RuntimeError):
    """影子链路取到与主链相同的模型；放弃本次影子（不产生无意义对比）。"""

def _compact_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        payload = exc.errors(include_url=False, include_input=False)
        # Pydantic 的 ctx 可能携带原始 ValueError，诊断文本必须可序列化，
        # 否则首次校验失败会反过来中断重试。
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    else:
        text = str(exc)
    return text.replace("\n", " ")[:800]
