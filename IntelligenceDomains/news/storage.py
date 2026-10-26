"""新闻领域结果后处理与 Event V4 存储实现。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from IntelligenceDomains.contracts import DomainPreparedResult
from ServiceComponent.event_v4_archive import ArchivedDataV4, build_archive_appendix
from ServiceComponent.event_v4_conversion import EventConversionResult, convert_event_extraction
from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME,
    APPENDIX_CONTENT_TIME_SOURCE,
    APPENDIX_EXTRACTION_QUALITY,
    APPENDIX_RAW_DATA,
    APPENDIX_TIME_ARCHIVED,
)
from ServiceComponent.intelligence_time import resolve_content_time


@dataclass(frozen=True, slots=True)
class NewsPreparedArchive:
    archive: ArchivedDataV4
    conversion: EventConversionResult


class NewsStorage:
    def __init__(self, event_registry: Any, *, repository_override: Any = None):
        self.event_registry = event_registry
        self.repository_override = repository_override

    def repository(self, ctx: Any) -> Any:
        repository = (
            self.repository_override
            or getattr(ctx, "event_v4_archive_repository", None)
        )
        if repository is None:
            raise RuntimeError("新闻领域仅配置为无持久化调试模式")
        return repository

    def contains_source(self, ctx: Any, informant: str) -> bool:
        return bool(self.repository(ctx).contains_source(informant))

    def contains_id(self, ctx: Any, intelligence_uuid: Any) -> bool:
        return bool(self.repository(ctx).contains_id(intelligence_uuid))

    def prepare(
        self, ctx: Any, original_data: dict, analysis: Any, *,
        prompt_version: int, ai_service: str, ai_model: str,
        intelligence_uuid: Any, informant: str,
        total_score: float | None, quality: dict | None,
        prompt_override: str | None = None,
    ) -> DomainPreparedResult:
        processed_at = datetime.now(timezone.utc)
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
        if quality is not None:
            appendix[APPENDIX_EXTRACTION_QUALITY] = quality
        archive = ArchivedDataV4(
            **analysis.model_dump(mode="python", by_alias=True),
            UUID=str(intelligence_uuid),
            INFORMANT=informant,
            APPENDIX=appendix,
        )
        output = archive.model_dump(mode="json", by_alias=True)
        output["debug_prompt"] = {
            "version": prompt_version,
            "overridden": bool(prompt_override),
        }
        if analysis.is_non_intelligence:
            return DomainPreparedResult(True, archive, output)

        conversion = convert_event_extraction(
            analysis.EVENT_EXTRACTION,
            intelligence_uuid=intelligence_uuid,
            registry=self.event_registry,
            observed_at=processed_at,
            metadata={"informant": informant, "subsystem": ctx.name},
        )
        output["events"] = [dict(item) for item in conversion.events]
        return DomainPreparedResult(
            False,
            NewsPreparedArchive(archive=archive, conversion=conversion),
            output,
        )

    def save_low_value(self, ctx: Any, payload: ArchivedDataV4) -> None:
        self.repository(ctx).save_low_value(payload)

    def commit(self, ctx: Any, payload: NewsPreparedArchive) -> ArchivedDataV4:
        if not isinstance(payload, NewsPreparedArchive):
            raise TypeError("invalid_news_archive_payload")
        archive = payload.archive.model_copy(deep=True)
        archived_at = datetime.now(timezone.utc)
        archive.APPENDIX[APPENDIX_TIME_ARCHIVED] = archived_at
        content_time, source = resolve_content_time(
            archive.APPENDIX.get(APPENDIX_RAW_DATA) or {}, archived_at)
        archive.APPENDIX[APPENDIX_CONTENT_TIME] = content_time
        archive.APPENDIX[APPENDIX_CONTENT_TIME_SOURCE] = source
        self.repository(ctx).commit(archive, payload.conversion.events)
        return archive

    @staticmethod
    def serialize(record: Any) -> dict[str, Any]:
        if hasattr(record, "model_dump"):
            return record.model_dump(mode="json", by_alias=True)
        return dict(record)
