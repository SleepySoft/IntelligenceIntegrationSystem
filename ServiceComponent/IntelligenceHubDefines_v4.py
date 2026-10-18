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
from ServiceComponent.intelligence_contracts import NonIntelligenceReason


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

# 维度 -> 按程度升序的 (等级词, 映射分, 释义, 准入/排除提示)。unknown 一律映射为 1：
# 缺证据按最低分处理，避免“信息越少分越高”的反向激励。
# v43 起 LLM 只输出等级词，分数由服务端按本表确定；v44 起为聚集档位补充准入/排除提示。
RATE_LEVELS: dict[str, tuple[tuple[str, int, str, str], ...]] = {
    "影响广度": (
        ("unknown", 1, "无足够范围信息", ""),
        ("individual", 2, "单个个人或对象", ""),
        ("organization", 3, "单个组织或设施", "影响单个组织或设施时应选此档而非 local"),
        ("local", 4, "局部地区或多个组织", "仅当明确涉及局部地区或跨多个组织；单一组织用 organization"),
        ("sectoral", 6, "主要行业或跨地区", ""),
        ("national", 7, "全国性范围", "需要正文有全国覆盖或全国性系统的证据，仅主体是全国性企业不算"),
        ("international", 8, "跨国或多市场", ""),
        ("systemic", 10, "系统性范围", ""),
    ),
    "影响深度": (
        ("unknown", 1, "无法判断实际后果", ""),
        ("nominal", 2, "仅名义或轻微变化", ""),
        ("limited", 4, "有限、局部、容易恢复", ""),
        ("material", 6, "产生明确业务或状态变化", "仅当正文描述已发生的明确后果；仅宣布/计划而未发生的用 limited 并加限定词"),
        ("severe", 8, "造成重大损失、中断或权利变化", ""),
        ("critical", 10, "造成关键能力丧失或不可逆后果", ""),
    ),
    "新颖性与异常性": (
        ("unknown", 1, "没有足够历史基线", ""),
        ("routine", 2, "常规重复事件", "例行会晤、例行演习、定期发布等多半属于此档"),
        ("uncommon", 4, "不常见但已有多次先例", "有先例即可选；同类事件近年反复出现时应用 routine"),
        ("rare", 6, "历史上较少发生", ""),
        ("exceptional", 8, "极少发生或组合异常", ""),
        ("unprecedented", 10, "没有可比先例", ""),
    ),
    "演化与连锁潜力": (
        ("unknown", 1, "缺少关系或依赖信息", ""),
        ("isolated", 2, "基本局限于当前事件", ""),
        ("contained", 4, "存在后续可能，但传播路径有限", "已出现向他方传播的实际迹象时应选 propagating"),
        ("conditional", 5, "依赖明确条件才会扩展", ""),
        ("propagating", 7, "已出现向其他主体或事件传播", ""),
        ("cascading", 8, "存在多级传播路径", ""),
        ("systemic", 10, "可能形成系统性连锁", ""),
    ),
    "舆情及认知影响": (
        ("unknown", 1, "没有传播信息", ""),
        ("unnoticed", 2, "尚未形成明显关注", ""),
        ("niche", 3, "局限于专业或小众群体", ""),
        ("visible", 5, "已形成可观察公众关注", "需要可观察的关注证据（多方报道、热搜、官方回应）；例行报道本身不算"),
        ("controversial", 7, "出现明显争议或叙事分化", ""),
        ("widespread", 8, "广泛传播并影响公众认知", ""),
        ("dominant", 10, "成为主导性公共议题或叙事", ""),
    ),
    "可行动性": (
        ("unknown", 1, "未指定行动主体或信息不足", ""),
        ("non_actionable", 1, "没有明确可执行动作", ""),
        ("expired", 2, "原本可行动，但行动窗口已结束", ""),
        ("monitorable", 4, "只能继续监控", "仅当正文没有任何明确行动主体、对象或窗口；存在可行行动时应选 actionable"),
        ("preparatory", 6, "可以提前准备或调整预案", ""),
        ("actionable", 8, "存在明确对象、窗口和可执行措施", ""),
        ("urgent", 10, "行动窗口很短，需要立即响应", ""),
    ),
}


def map_rate_levels(rate: Any) -> dict[str, int]:
    """把六维度的等级词确定射为整数分；维度集合或等级词非法时报错。"""
    if not isinstance(rate, dict) or set(rate) != RATE_DIMENSIONS:
        raise ValueError("RATE must contain exactly the six defined score dimensions")
    mapped: dict[str, int] = {}
    for dimension, level in rate.items():
        table = {item[0]: item[1] for item in RATE_LEVELS[dimension]}
        if level not in table:
            raise ValueError(f"RATE[{dimension}] 未登记等级词: {level!r}")
        mapped[dimension] = table[level]
    return mapped


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
    NON_INTELLIGENCE_REASON: NonIntelligenceReason | None = None
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

        if self.NON_INTELLIGENCE_REASON is not None:
            raise ValueError("valuable intelligence must not contain NON_INTELLIGENCE_REASON")

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
