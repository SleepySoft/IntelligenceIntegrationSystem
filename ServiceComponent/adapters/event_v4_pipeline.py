"""Event V4 主链适配器；与旧 IIS 新闻式流程保持独立。"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

from pydantic import ValidationError
from AIClientCenter.core.rate_limit import estimate_message_tokens
from IntelligenceDomains import (
    DomainRegistry,
    PreparedDomainArchive,
    build_domain_registry,
)

from ServiceComponent.collected_data import CollectedData
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
from ServiceComponent.adapters.base_pipeline import BasePipelinePorts
from ServiceComponent.manual_debug_analysis import MANUAL_TEST_SOURCE
from ServiceComponent.extraction_quality import compute_extraction_quality
from ServiceComponent.intelligence_contracts import (
    APPENDIX_FIELD,
    APPENDIX_PROMPT_VERSION,
    APPENDIX_RETRY_FIRST_FAILED_AT,
    APPENDIX_RETRY_LAST_ERROR,
    APPENDIX_RETRY_LEASE_UNTIL,
    APPENDIX_RETRY_LEVEL,
    APPENDIX_RETRY_NEXT_AT,
    CACHE_ARCHIVED_FLAG_FIELD,
    CacheArchiveState,
    EventV4CollectionSuffix,
    appendix_path,
)
from ServiceComponent.intelligence_identity import normalize_or_assign_intelligence_uuid
from ServiceComponent.pipeline.contracts import StageResult
from ServiceComponent.runtime.events import HubEvent


logger = logging.getLogger(__name__)


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
        domain_resources: Any,
        ai_client_manager: Any,
        *,
        domain_registry: DomainRegistry | None = None,
        archive_repository: Any = None,
        event_registry: Any = None,
        prompt_catalog: Any = None,
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
        scorer_factory_override = scorer_factory
        super().__init__(
            domain_resources,
            ai_client_manager,
            analyzer=analyzer,
            transient_analyzer=transient_analyzer,
            scorer_factory=scorer_factory,
            retry_wait=retry_wait,
            client_wait_timeout=client_wait_timeout,
            analysis_worker_count=analysis_worker_count,
        )
        self._failure_recorder = failure_recorder
        enabled_contexts = getattr(domain_resources, "enabled_domains", None)
        if not callable(enabled_contexts):
            enabled_contexts = getattr(domain_resources, "enabled_subsystems", None)
        if callable(enabled_contexts):
            contexts = enabled_contexts()
        else:
            legacy_context = getattr(domain_resources, "context", None)
            if legacy_context is None:
                legacy_context = getattr(domain_resources, "ctx", None)
            contexts = [legacy_context] if legacy_context is not None else []
        default_name = getattr(domain_resources, "default_name", None)
        if not default_name and contexts:
            default_name = contexts[0].name
        self._domain_registry = domain_registry or build_domain_registry(
            contexts,
            default_name=default_name or "news",
            factory_kwargs={
                "archive_repository": archive_repository,
                "event_registry": event_registry,
                "prompt_catalog": prompt_catalog,
                "prompt_table": prompt_table,
                "extra_prompt_versions": extra_prompt_versions,
                "extraction_modes": extraction_modes,
                "scorer_factory": scorer_factory_override,
            },
        )
        # 兼容旧扩展和测试的只读视图；生产路径始终按 ctx.name 查领域定义。
        self._prompt_catalog = self._domain_registry.default().prompt_catalog
        self._prompt_table = self._prompt_catalog.sampling_table()
        self._selectable_prompts = self._prompt_catalog.selectable_table()
        # 影子链路：与主链并行执行同构分析，结果只落盘对比，不归档、不影响主链。
        self._shadow_versions = [int(version) for version in (shadow_versions or ())]
        unknown_shadow = [
            (name, version)
            for name in self._domain_registry.names()
            for version in self._shadow_versions
            if self._domain_registry.require(name).subsystems.shadow is not None
            and not self._domain_registry.require(name).prompt_catalog.has(version)
        ]
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
        # 兼容保留旧构造参数，但不再改写权威 EVENT_EXTRACTION 的原始实体名。
        # 名称归一和上下文消歧由非破坏性的 EntityResolution 解析视图完成。
        self._entity_name_normalizer = entity_name_normalizer

    def set_entity_name_normalizer(
        self, normalizer: Callable[[str], str | None] | None,
    ) -> None:
        """由组合根注入/移除可选的实体标准名解析器。"""
        self._entity_name_normalizer = normalizer

    def domain_definition(self, domain_name: str | None):
        """供 Batch/调试等附属机制读取同一领域定义。"""
        return self._domain_registry.require(domain_name)

    def _domain_for_context(self, ctx: Any):
        return self.domain_definition(getattr(ctx, "name", None))

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
        data["domain"] = ctx.name
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
            return StageResult.allow(data, domain_name=ctx.name, lane="replay")
        return StageResult.allow(data, domain_name=ctx.name)

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
                "domain_name": getattr(ctx, "name", ""),
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

    def get_transient_prompt_table(self, domain_name: str) -> dict[int, str]:
        ctx = self._domain_resources.resolve(domain_name)
        if ctx is None:
            raise ValueError(f"unknown_domain:{domain_name!r}")
        return dict(self._domain_for_context(ctx).prompt_catalog.selectable_table())

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
        storage = self._domain_for_context(ctx).storage
        if persist and storage.contains_source(ctx, informant):
            self._mark_cache(original_data.get("UUID", ""), CacheArchiveState.DUPLICATED, ctx)
            self._increment_stat(ctx, "dropped")
            return StageResult.reject("already_archived")
        if persist:
            while storage.contains_id(ctx, intelligence_uuid):
                previous_uuid = original_data["UUID"]
                intelligence_uuid = normalize_or_assign_intelligence_uuid(None)
                original_data["UUID"] = str(intelligence_uuid)
                if ctx.mongo_db_cache:
                    ctx.mongo_db_cache.update(
                        {"UUID": previous_uuid}, {"UUID": original_data["UUID"]})
        try:
            if persist:
                requested_prompt_version = self._persistent_prompt_version(
                    ctx, original_data)
                prompt_override = None
            else:
                requested_prompt_version, prompt_override = (
                    self._transient_prompt_options(original_data)
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
            return self.prepare_analysis_outcome(
                ctx, original_data, analysis,
                prompt_version=prompt_version, ai_service=ai_service,
                ai_model=ai_model, intelligence_uuid=intelligence_uuid,
                informant=informant, persist=persist,
                prompt_override=prompt_override,
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
                if retry_state == CacheArchiveState.ERROR.value:
                    recorder = self._analysis_stats
                    marker = getattr(recorder, "record_terminal_failure", None)
                    if callable(marker):
                        try:
                            marker(
                                uuid=str(original_data.get("UUID") or ""),
                                domain_name=getattr(ctx, "name", ""), shadow=False)
                        except Exception:
                            pass
                self._increment_stat(ctx, "error")
                return StageResult.reject(
                    f"analysis_{retry_state.lower()}:{type(exc).__name__}:{exc}")
            return StageResult.reject(f"analysis_exception:{type(exc).__name__}:{exc}")

    def _persistent_prompt_version(self, ctx: Any, original_data: dict) -> int:
        """首次分析分配一次 Prompt，持久化重试和重启恢复继续使用同一版本。"""
        raw_appendix = original_data.get(APPENDIX_FIELD)
        appendix = dict(raw_appendix) if isinstance(raw_appendix, Mapping) else {}
        assigned = appendix.get(APPENDIX_PROMPT_VERSION)
        try:
            assigned_version = int(assigned) if assigned is not None else None
        except (TypeError, ValueError):
            assigned_version = None
        catalog = self._domain_for_context(ctx).prompt_catalog
        if assigned_version is not None and catalog.has(assigned_version):
            appendix[APPENDIX_PROMPT_VERSION] = assigned_version
            original_data[APPENDIX_FIELD] = appendix
            return assigned_version
        if assigned is not None:
            logger.warning(
                "Ignore unavailable persisted prompt version %r for intelligence %s",
                assigned, original_data.get("UUID"),
            )

        version = random.choice(catalog.sampling_versions())
        appendix[APPENDIX_PROMPT_VERSION] = version
        original_data[APPENDIX_FIELD] = appendix
        storage = getattr(ctx, "mongo_db_cache", None)
        uuid = str(original_data.get("UUID") or "")
        if storage is not None and uuid:
            try:
                storage.update(
                    {"UUID": uuid},
                    {appendix_path(APPENDIX_PROMPT_VERSION): version},
                )
            except Exception:
                # 分配写回属于灰度归因元数据；失败不能阻塞生产分析主链。
                logger.warning(
                    "Persist prompt assignment failed for intelligence %s; analysis continues",
                    uuid, exc_info=True,
                )
        return version

    def prepare_analysis_outcome(self, ctx: Any, original_data: dict,
                                 analysis: Any, *,
                                 prompt_version: int, ai_service: str,
                                 ai_model: str, intelligence_uuid: Any,
                                 informant: str, persist: bool,
                                 prompt_override: str | None = None) -> StageResult:
        """分析成功后的公共后段：低价值分流 / 事件转换 / 评分 / 附录 / 归档载荷。

        实时主链（_analyze_v4）与批量回注（BatchAnalysisService）共用，
        保证两条路径的归档结构与质量统计完全一致。
        """
        definition = self._domain_for_context(ctx)
        total_score = None
        if not analysis.is_non_intelligence:
            total_score = definition.score(
                analysis, getattr(ctx, "scoring_config", None))
        quality = self._compute_quality(ctx, analysis, original_data)
        prepared = definition.storage.prepare(
            ctx, original_data, analysis,
            prompt_version=prompt_version,
            ai_service=ai_service,
            ai_model=ai_model,
            intelligence_uuid=intelligence_uuid,
            informant=informant,
            total_score=total_score,
            quality=quality,
            prompt_override=prompt_override,
        )
        if not persist:
            return StageResult.allow(
                dict(prepared.output), domain_name=ctx.name, transient=True,
                low_value=prepared.low_value,
            )
        if prepared.low_value:
            definition.storage.save_low_value(ctx, prepared.archive_payload)
            self._mark_cache(original_data.get("UUID", ""), CacheArchiveState.DROP, ctx)
            self._increment_stat(ctx, "dropped")
            return StageResult.reject("non_intelligence")
        return StageResult.allow(
            PreparedDomainArchive(
                domain_name=ctx.name,
                payload=prepared.archive_payload,
                intelligence_uuid=str(intelligence_uuid),
                retry_metadata=dict(original_data.get(APPENDIX_FIELD) or {}),
            ),
            domain_name=ctx.name,
        )

    def archive(self, event: HubEvent) -> StageResult:
        ctx = self._resolve_context(event)
        prepared = event.payload
        if (not isinstance(prepared, PreparedDomainArchive)
                or prepared.domain_name != ctx.name):
            self._increment_stat(ctx, "error")
            return StageResult.reject("invalid_domain_archive_payload")
        try:
            definition = self._domain_for_context(ctx)
            archive = definition.storage.commit(ctx, prepared.payload)
            source_uuid = prepared.intelligence_uuid
            self._mark_cache(source_uuid, CacheArchiveState.ARCHIVED, ctx)
            self._increment_stat(ctx, "archived")
            return StageResult.allow(
                definition.storage.serialize(archive),
                domain_name=ctx.name,
            )
        except Exception as exc:
            source_uuid = prepared.intelligence_uuid
            retry_state = self._schedule_retry(
                ctx, source_uuid, {APPENDIX_FIELD: prepared.retry_metadata}, exc)
            self._increment_stat(ctx, "error")
            return StageResult.reject(
                f"archive_{retry_state.lower()}:{type(exc).__name__}:{exc}")

    def _require_archive_repository(self, ctx: Any) -> Any:
        """旧 Batch 扩展兼容入口；新代码应通过领域 storage 查询。"""
        return self._domain_for_context(ctx).storage.repository(ctx)

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
                                 error_class: str | None,
                                 request_id: str | None = None,
                                 client_name: str | None = None) -> None:
        """每次 AI 调用落一条统计；任何失败都不影响主链。"""
        recorder = self._analysis_stats
        if recorder is None:
            return
        try:
            recorder.record_attempt(
                uuid=str(original_data.get("UUID") or ""),
                domain_name=getattr(ctx, "name", ""), shadow=shadow,
                prompt_version=prompt_version, ai_service=service, ai_model=model,
                attempt_no=attempt, latency_ms=latency_ms, error_class=error_class,
                request_id=request_id, client_name=client_name)
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
                domain_name=getattr(ctx, "name", ""), shadow=shadow,
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
    ) -> tuple[Any, int, str, str, str]:
        catalog = self._domain_for_context(ctx).prompt_catalog
        if prompt_version is None:
            prompt_version = random.choice(catalog.sampling_versions())
        elif not catalog.has(prompt_version):
            raise ValueError(f"prompt_version_not_configured:{prompt_version}")
        protocol = catalog.protocol_for(prompt_version)
        base_prompt = (
            prompt_override if prompt_override is not None
            else catalog.text_for(prompt_version)
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
            attempt_recorded = False
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
                        error_class=AnalysisErrorClass.CONTENT_SAFETY,
                        request_id=request_id,
                        client_name=getattr(ai_client, "name", ""))
                    attempt_recorded = True
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
                    validated = self._validate_analysis(ctx, raw_result, prompt_version)
                    self._report_client_feedback(
                        ai_client, request_id, valid=True,
                        labels={"stage": "event_v4_validation", "attempt": attempt},
                    )
                    self._change_runtime_stat("validated")
                    self._record_analysis_attempt(
                        original_data=original_data, ctx=ctx, shadow=shadow,
                        prompt_version=prompt_version, service=service, model=model,
                        attempt=attempt, latency_ms=call_ms,
                        error_class=None, request_id=request_id,
                        client_name=getattr(ai_client, "name", ""))
                    attempt_recorded = True
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
                        error_class=attempt_error_class, request_id=request_id,
                        client_name=getattr(ai_client, "name", ""))
                    attempt_recorded = True
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
            if not attempt_recorded:
                self._record_analysis_attempt(
                    original_data=original_data, ctx=ctx, shadow=shadow,
                    prompt_version=prompt_version, service=service, model=model,
                    attempt=attempt, latency_ms=call_ms,
                    error_class=attempt_error_class or AnalysisErrorClass.UNKNOWN,
                    request_id=request_id,
                    client_name=getattr(ai_client, "name", ""),
                )
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

    def _compute_quality(self, ctx: Any, analysis: Any,
                         original_data: dict) -> dict | None:
        """计算抽取质量统计；仅供后续聚合参考，任何异常都不得影响主链。"""
        if not self._quality_enabled or analysis.EVENT_EXTRACTION is None:
            return None
        raw_text = f"{original_data.get('title') or ''}\n{original_data.get('content') or ''}"
        try:
            return compute_extraction_quality(
                analysis.EVENT_EXTRACTION,
                self._domain_for_context(ctx).event_registry,
                raw_text=raw_text)
        except Exception:
            logger.exception("extraction quality check failed; ignored")
            return None

    def _run_shadow_analysis(self, ctx: Any, event: HubEvent, original_data: dict, *,
                             primary_version: int, persist: bool,
                             primary_client_name: str = "",
                             primary_model: str = "") -> None:
        """主链分析成功后并行执行影子链路，结果仅落盘对比。任何失败都不影响主链。"""
        definition = self._domain_for_context(ctx)
        if (not persist or self._shadow_recorder is None
                or definition.subsystems.shadow is None):
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
                    domain_name=ctx.name,
                    primary_prompt_version=primary_version,
                    shadow_prompt_version=version,
                    analysis=analysis.model_dump(mode="json", by_alias=True),
                    ai_service=service,
                    ai_model=model,
                    extraction_quality=self._compute_quality(ctx, analysis, original_data),
                )
                with self._shadow_stats_lock:
                    self._shadow_stats["recorded"] += 1
            except Exception:
                with self._shadow_stats_lock:
                    self._shadow_stats["failed"] += 1
                logger.warning("shadow analysis failed for prompt v%s", version, exc_info=True)

    def _validate_analysis(self, ctx: Any, raw_result: dict, prompt_version: int) -> Any:
        """按所选版本绑定的协议做后处理（桥接）与校验。

        归档与下游消费的始终是当前领域协议返回的规范结果；各领域差异封装在
        PromptProtocol.validate_result 内，公共流程不依赖具体结果类型。
        """
        return self._domain_for_context(ctx).prompt_catalog.protocol_for(
            prompt_version).validate_result(raw_result)

    def _normalize_entity_names(self, analysis: Any) -> Any:
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
            domain_name=ctx.name,
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
