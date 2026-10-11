"""IIS 采集输入与 Event V4 分析结果契约。

Event V4 在 V2 分析结果上只增加 ``EVENT_EXTRACTION``。事件、人物、机构和地点
速查字段由归档层从该字段派生，不要求模型重复输出。
"""

import datetime
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from event_engine.domains import registry_for
from event_engine.extraction import (
    EventExtractionResult,
    event_extraction_json_schema,
    validate_event_extraction,
)
from ServiceComponent.intelligence_time import normalize_intelligence_datetime


DEFAULT_EVENT_REGISTRY = registry_for("news", "industry", "financial")
NON_INTELLIGENCE_TAXONOMY = "NonIntelligence"
TAXONOMY_SUBCATEGORIES = {
    "政治与安全": {"国际博弈", "国内政局", "国防军事", "法律与合规", "战略认知", "重大犯罪与恐怖主义"},
    "经济与金融": {"宏观经济", "商业与市场", "能源与资源", "交通与物流", "农业与粮食"},
    "科技与网络": {"前沿科技", "信息安全", "数字基础设施"},
    "社会与环境": {"社会民生", "公共卫生", "自然灾害与环境", "教育与文化"},
}
RATE_DIMENSIONS = {
    "影响广度",
    "影响深度",
    "新颖性与异常性",
    "演化与连锁潜力",
    "舆情及认知影响",
    "可行动性",
}


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class CollectedData(BaseModel):
    """Raw collector input shared by every subsystem and analysis version."""

    model_config = ConfigDict(extra="ignore")

    UUID: str = Field(..., min_length=1)
    token: str = Field(..., min_length=1)
    source: str | None = None
    target: str | None = None
    prompt: str | None = None
    title: str = Field(..., min_length=1)
    authors: list[str] = Field(default_factory=list)
    content: str = Field(..., min_length=10)
    pub_time: Any | None = None
    collect_time: datetime.datetime | None = Field(default_factory=_utc_now)
    informant: str = Field(..., min_length=1)
    subsystem: str = ""
    temp_data: dict = Field(default_factory=dict)

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


class AnalysisResultV4(StrictModel):
    """V2-compatible AI output with Event Engine extraction added.

    ``TIME``、``LOCATION``、``PEOPLE``、``ORGANIZATION`` 不属于模型输出；它们由
    归档层从 ``EVENT_EXTRACTION`` 生成。``GEOGRAPHY`` 已删除。
    """

    TAXONOMY: str = Field(..., min_length=1)
    SUB_CATEGORY: list[str] = Field(default_factory=list, max_length=5)
    REASON: str | None = Field(None, max_length=100)
    EVENT_TITLE: str | None = Field(None, min_length=1, max_length=30)
    EVENT_BRIEF: str | None = Field(None, min_length=1, max_length=100)
    EVENT_TEXT: str | None = Field(None, min_length=1, max_length=2000)
    IMPACT: str | None = Field(None, min_length=1, max_length=100)
    RATE: dict[str, int] = Field(default_factory=dict)
    TIPS: str | None = Field(None, max_length=100)
    EVENT_EXTRACTION: EventExtractionResult | None = None

    @property
    def is_non_intelligence(self) -> bool:
        return self.TAXONOMY == NON_INTELLIGENCE_TAXONOMY

    @model_validator(mode="after")
    def validate_branch(self):
        if self.is_non_intelligence:
            if not self.REASON:
                raise ValueError("NonIntelligence result requires REASON")
            if self.EVENT_EXTRACTION is not None:
                raise ValueError("NonIntelligence result must not contain EVENT_EXTRACTION")
            return self

        if self.TAXONOMY not in TAXONOMY_SUBCATEGORIES:
            raise ValueError(f"unsupported TAXONOMY: {self.TAXONOMY}")
        if not 1 <= len(self.SUB_CATEGORY) <= 5:
            raise ValueError("valuable intelligence requires 1 to 5 SUB_CATEGORY values")
        if not TAXONOMY_SUBCATEGORIES[self.TAXONOMY].intersection(self.SUB_CATEGORY):
            raise ValueError("at least one SUB_CATEGORY must belong to TAXONOMY")
        if set(self.RATE) != RATE_DIMENSIONS:
            raise ValueError("RATE must contain exactly the six defined score dimensions")
        if any(isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10
               for value in self.RATE.values()):
            raise ValueError("every RATE value must be an integer from 1 to 10")

        required = {
            "SUB_CATEGORY": self.SUB_CATEGORY,
            "REASON": self.REASON,
            "EVENT_TITLE": self.EVENT_TITLE,
            "EVENT_BRIEF": self.EVENT_BRIEF,
            "EVENT_TEXT": self.EVENT_TEXT,
            "IMPACT": self.IMPACT,
            "RATE": self.RATE,
            "EVENT_EXTRACTION": self.EVENT_EXTRACTION,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"valuable intelligence missing fields: {', '.join(missing)}")
        return self


def validate_analysis_result_v4(data: Any) -> AnalysisResultV4:
    result = AnalysisResultV4.model_validate(data)
    if result.EVENT_EXTRACTION is not None:
        validate_event_extraction(result.EVENT_EXTRACTION, DEFAULT_EVENT_REGISTRY)
    return result


def event_v4_json_schema() -> dict[str, Any]:
    schema = AnalysisResultV4.model_json_schema()
    event_schema = event_extraction_json_schema(DEFAULT_EVENT_REGISTRY)
    predicate = event_schema.get("$defs", {}).get("ExtractedPredicate")
    if predicate and "ExtractedPredicate" in schema.get("$defs", {}):
        schema["$defs"]["ExtractedPredicate"] = predicate
    return schema
