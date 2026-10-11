"""IIS 采集输入与 Event V4 分析结果。

``CollectedData`` 是不随分析版本变化的采集契约；事件抽取子结构由 event_engine 独立
定义，本模块同时组合 Event V4 的消息、分类和评估字段。
"""

import datetime
import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from event_engine.domains import registry_for
from event_engine.extraction import (
    EventExtractionResult,
    event_extraction_json_schema,
    validate_event_extraction,
)
from ServiceComponent.intelligence_time import normalize_intelligence_datetime


DEFAULT_EVENT_REGISTRY = registry_for("news", "industry", "financial")


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class CollectedData(BaseModel):
    """Raw collector input shared by every subsystem and analysis version."""

    model_config = ConfigDict(extra="ignore")

    UUID: str = Field(..., min_length=1,
                      description="[MUST]: Unique identifier for the message/article.")
    token: str = Field(..., min_length=1,
                       description="[MUST]: Validation token for the endpoint/source.")
    source: str | None = Field(
        None, description="(Optional): Origin platform (e.g., 'Twitter', 'BBC').")
    target: str | None = Field(None, description="(Optional): Intended recipient or channel.")
    prompt: str | None = Field(
        None, description="(Optional): Custom prompt override for this specific item.")
    title: str = Field(..., min_length=1, description="[MUST]: Raw title of the article.")
    authors: list[str] = Field(
        default_factory=list, description="(Optional): List of authors found in metadata.")
    content: str = Field(
        ..., min_length=10, description="[MUST]: The main text body to be analyzed by LLM.")
    pub_time: Any | None = Field(None, description="(Optional): Original publish time.")
    collect_time: datetime.datetime | None = Field(
        default_factory=_utc_now, description="(Optional): Content collect time in UTC.")
    informant: str = Field(
        ..., min_length=1, description="[MUST]: The specific source URL or informant ID.")
    subsystem: str = Field(
        "", description="(Optional): Target subsystem name. Empty means the default subsystem. "
                        "Examples: 'news', 'finance', 'industry'.")
    temp_data: dict = Field(
        default_factory=dict, description="(Optional): Temporary data for data management.")

    @field_validator("pub_time", mode="before")
    @classmethod
    def convert_any_time_format(cls, value):
        if value is None or isinstance(value, datetime.datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.datetime.fromtimestamp(value)
        if isinstance(value, time.struct_time):
            return datetime.datetime.fromtimestamp(time.mktime(value))
        if isinstance(value, str):
            try:
                return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S"):
                    try:
                        return datetime.datetime.strptime(value, fmt)
                    except ValueError:
                        continue
        return value

    @field_validator("collect_time", mode="before")
    @classmethod
    def normalize_collect_time(cls, value):
        if value is None:
            return None
        return normalize_intelligence_datetime(value) or value

    @field_validator("informant", mode="before")
    @classmethod
    def normalize_informant(cls, value):
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("informant/source URL cannot be empty")
        return normalized


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class MessageAnalysisV4(StrictModel):
    title: str = Field(..., min_length=1, max_length=30)
    brief: str = Field(..., min_length=1, max_length=100)
    text: str = Field(..., min_length=1, max_length=2000)


MainTaxonomy = Literal["政治与安全", "经济与金融", "科技与网络", "社会与环境"]
SubCategory = Literal[
    "国际博弈", "国内政局", "国防军事", "法律与合规", "战略认知", "重大犯罪与恐怖主义",
    "宏观经济", "商业与市场", "能源与资源", "交通与物流", "农业与粮食",
    "前沿科技", "信息安全", "数字基础设施",
    "社会民生", "公共卫生", "自然灾害与环境", "教育与文化",
]

TAXONOMY_SUBCATEGORIES = {
    "政治与安全": {"国际博弈", "国内政局", "国防军事", "法律与合规", "战略认知", "重大犯罪与恐怖主义"},
    "经济与金融": {"宏观经济", "商业与市场", "能源与资源", "交通与物流", "农业与粮食"},
    "科技与网络": {"前沿科技", "信息安全", "数字基础设施"},
    "社会与环境": {"社会民生", "公共卫生", "自然灾害与环境", "教育与文化"},
}


class ClassificationV4(StrictModel):
    taxonomy: MainTaxonomy
    subcategories: list[SubCategory] = Field(..., min_length=1, max_length=5)

    @model_validator(mode="after")
    def check_subcategories(self):
        if not (set(self.subcategories) & TAXONOMY_SUBCATEGORIES[self.taxonomy]):
            raise ValueError("at least one subcategory must belong to taxonomy")
        return self


class IntelligenceRateV4(StrictModel):
    impact_scope: int = Field(..., alias="影响广度", ge=1, le=10)
    impact_severity: int = Field(..., alias="影响深度", ge=1, le=10)
    novelty: int = Field(..., alias="新颖性与异常性", ge=1, le=10)
    evolution: int = Field(..., alias="演化与连锁潜力", ge=1, le=10)
    sentiment: int = Field(..., alias="舆情及认知影响", ge=1, le=10)
    actionability: int = Field(..., alias="可行动性", ge=1, le=10)


class IntelligenceAssessmentV4(StrictModel):
    impact: str = Field(..., min_length=1, max_length=100)
    reason: str = Field(..., min_length=1, max_length=100)
    rate: IntelligenceRateV4
    tips: str = Field(..., max_length=100)


class ValuableIntelligenceV4(StrictModel):
    kind: Literal["valuable"]
    message: MessageAnalysisV4
    classification: ClassificationV4
    assessment: IntelligenceAssessmentV4
    event_extraction: EventExtractionResult


class NonIntelligenceV4(StrictModel):
    kind: Literal["non_intelligence"]
    reason: str = Field(..., min_length=1, max_length=100)


AnalysisResultV4 = Annotated[
    ValuableIntelligenceV4 | NonIntelligenceV4,
    Field(discriminator="kind"),
]
ANALYSIS_RESULT_V4_ADAPTER = TypeAdapter(AnalysisResultV4)


def validate_analysis_result_v4(data: Any):
    result = ANALYSIS_RESULT_V4_ADAPTER.validate_python(data)
    if isinstance(result, ValuableIntelligenceV4):
        validate_event_extraction(result.event_extraction, DEFAULT_EVENT_REGISTRY)
    return result


def event_v4_json_schema() -> dict[str, Any]:
    schema = ANALYSIS_RESULT_V4_ADAPTER.json_schema()
    event_schema = event_extraction_json_schema(DEFAULT_EVENT_REGISTRY)
    predicate = event_schema.get("$defs", {}).get("ExtractedPredicate")
    if predicate and "ExtractedPredicate" in schema.get("$defs", {}):
        schema["$defs"]["ExtractedPredicate"] = predicate
    return schema
