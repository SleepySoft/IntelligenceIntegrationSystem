"""行业情报领域的分析结果契约与确定性 RATE 映射。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from event_engine.extraction import EventExtractionResult, validate_event_extraction
from ServiceComponent.intelligence_contracts import NonIntelligenceReason

from .predicates import build_industry_event_registry


DEFAULT_EVENT_REGISTRY = build_industry_event_registry()
NON_INTELLIGENCE_TAXONOMY = "NonIntelligence"
INDUSTRY_TAXONOMY = "行业情报"

INDUSTRY_SUBCATEGORIES = {
    "产品与技术",
    "产量与产能",
    "设施与扩张",
    "组织与人事",
    "并购与资产",
    "财务困境",
    "战略与市场",
}

RATE_DIMENSIONS = {
    "行动实质性",
    "规模与力度",
    "事实确定性",
    "认知增量",
    "预期差与异常性",
    "经营与战略影响",
    "投资可用性",
    "关注度与故事性",
}

# 维度 -> 按程度升序的 (等级词, 映射分, 释义, 准入/排除提示)。模型只选等级词，
# 服务端完成数值映射，避免同一描述在不同调用中产生漂移。
RATE_LEVELS: dict[str, tuple[tuple[str, int, str, str], ...]] = {
    "行动实质性": (
        ("unknown", 1, "无法确认具体行动", "证据不足时使用"),
        ("rhetoric", 2, "愿景、口号或非约束性表态", "没有资源或执行动作不得升档"),
        ("routine", 3, "常规经营动作", "例行发布或小幅调整"),
        ("concrete", 5, "对象和动作明确", "至少明确对象、范围或时间之一"),
        ("material", 8, "显著改变经营状态", "影响产品、人员、资产、设施或市场状态"),
        ("transformative", 10, "改变企业基本经营方向", "仅用于根本性转型或存续变化"),
    ),
    "规模与力度": (
        ("unknown", 1, "正文没有规模证据", "不得按公司知名度推断"),
        ("trivial", 2, "极小或试点规模", ""),
        ("limited", 4, "局部业务或小规模调整", ""),
        ("substantial", 6, "对相关业务具有明显规模", "需要人数、金额、比例、产量或设施证据"),
        ("large", 8, "覆盖多设施、多地区或主要业务", ""),
        ("massive", 10, "接近全公司或核心业务尺度", "不得仅因主体是大公司而选择"),
    ),
    "事实确定性": (
        ("unsupported", 1, "没有可识别来源", "此档通常应判为无情报价值"),
        ("anonymous", 2, "仅有匿名或单一间接消息源", "必须保留 epistemic 限定"),
        ("attributed", 4, "有明确可归因来源的报道", "公司尚未确认"),
        ("corroborated", 6, "多个独立来源或文件相互印证", ""),
        ("official", 8, "公司、监管、法院或交易所正式披露", "宣布不等于已经执行"),
        ("realized", 10, "行动已经发生且有结果证据", "必须同时用 phase 表达阶段"),
    ),
    "认知增量": (
        ("none", 1, "没有新增认识", ""),
        ("repeated", 2, "重复已知事实", "无进展的回顾或汇编"),
        ("incremental", 4, "补充少量新事实", ""),
        ("meaningful", 6, "明显更新对经营状态的认识", ""),
        ("thesis_changing", 8, "足以修正既有判断", "需要决定性新增事实"),
        ("fundamental", 10, "颠覆对企业基本状态的认识", "极少使用"),
    ),
    "预期差与异常性": (
        ("unknown", 1, "没有可靠比较基线", "不得使用外部记忆补基线"),
        ("expected", 2, "符合正文所述既有计划或常态", ""),
        ("mildly_surprising", 4, "存在轻度偏离", ""),
        ("surprising", 6, "明显偏离此前方向或一般预期", "正文须给出比较依据"),
        ("reversal", 8, "公司方向发生明确反转", ""),
        ("shock", 10, "突然且根本性的异常变化", "不得把普通裁员一概视为冲击"),
    ),
    "经营与战略影响": (
        ("unknown", 1, "无法判断经营含义", ""),
        ("negligible", 2, "对经营几乎无影响", ""),
        ("local", 4, "影响局部团队、产品或设施", ""),
        ("material", 6, "影响收入、成本、能力、资产或竞争位置", "需要明确因果路径"),
        ("major", 8, "影响核心业务或公司方向", ""),
        ("existential", 10, "涉及企业存续或核心能力丧失", "破产、清算等才可能适用"),
    ),
    "投资可用性": (
        ("none", 1, "不能支持投资判断", ""),
        ("background", 2, "仅作一般背景", ""),
        ("watchlist", 4, "形成可持续跟踪的观察项", ""),
        ("thesis_input", 6, "可修正收入、成本、资本开支或风险假设", ""),
        ("decision_relevant", 8, "对投资逻辑有直接影响", "必须说明受影响变量"),
        ("time_sensitive", 10, "存在很短的信息或决策窗口", "不得给出买卖建议"),
    ),
    "关注度与故事性": (
        ("none", 1, "没有额外关注价值", ""),
        ("routine", 2, "常规公司动态", ""),
        ("notable", 4, "具有可读的组织或经营信号", ""),
        ("compelling", 6, "存在鲜明冲突、转折或人事信号", ""),
        ("high_interest", 8, "大规模裁员、突然离任、关厂或战略反转等", "仍须有经营相关事实"),
        ("exceptional", 10, "极强且罕见的商业故事", "猎奇本身不能升档"),
    ),
}


def map_rate_levels(rate: Any) -> dict[str, int]:
    if not isinstance(rate, dict) or set(rate) != RATE_DIMENSIONS:
        raise ValueError("RATE must contain exactly the eight industry dimensions")
    mapped: dict[str, int] = {}
    for dimension, level in rate.items():
        table = {item[0]: item[1] for item in RATE_LEVELS[dimension]}
        if level not in table:
            raise ValueError(f"RATE[{dimension}] 未登记等级词: {level!r}")
        mapped[dimension] = table[level]
    return mapped


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class IndustryAnalysisResultV4(StrictModel):
    """行业领域结果；公共展示字段保持一致，RATE 与分类独立。"""

    TAXONOMY: Literal["行业情报", "NonIntelligence"]
    SUB_CATEGORY: list[str] = Field(default_factory=list, max_length=3)
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
            if self.NON_INTELLIGENCE_REASON in (None, NonIntelligenceReason.UNSPECIFIED):
                raise ValueError("NonIntelligence result requires NON_INTELLIGENCE_REASON")
            if self.EVENT_EXTRACTION is not None:
                raise ValueError("NonIntelligence result must not contain EVENT_EXTRACTION")
            if self.RATE:
                raise ValueError("NonIntelligence result RATE must be empty")
            return self

        if self.NON_INTELLIGENCE_REASON is not None:
            raise ValueError("valuable intelligence must not contain NON_INTELLIGENCE_REASON")
        if not 1 <= len(self.SUB_CATEGORY) <= 3:
            raise ValueError("industry intelligence requires 1 to 3 SUB_CATEGORY values")
        unknown = set(self.SUB_CATEGORY) - INDUSTRY_SUBCATEGORIES
        if unknown:
            raise ValueError(f"unsupported industry SUB_CATEGORY: {sorted(unknown)}")
        if set(self.RATE) != RATE_DIMENSIONS:
            raise ValueError("RATE must contain exactly the eight industry dimensions")
        if any(isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10
               for value in self.RATE.values()):
            raise ValueError("every RATE value must be an integer from 1 to 10")
        required = {
            "REASON": self.REASON,
            "EVENT_TITLE": self.EVENT_TITLE,
            "EVENT_BRIEF": self.EVENT_BRIEF,
            "EVENT_TEXT": self.EVENT_TEXT,
            "IMPACT": self.IMPACT,
            "EVENT_EXTRACTION": self.EVENT_EXTRACTION,
        }
        missing = [name for name, value in required.items() if not value]
        if self.TIPS is None:
            missing.append("TIPS")
        if missing:
            raise ValueError(f"industry intelligence missing fields: {', '.join(missing)}")
        return self


def validate_industry_analysis_result(
    data: Any, *, registry=DEFAULT_EVENT_REGISTRY,
) -> IndustryAnalysisResultV4:
    result = IndustryAnalysisResultV4.model_validate(data)
    if result.EVENT_EXTRACTION is not None:
        validate_event_extraction(result.EVENT_EXTRACTION, registry)
    return result


__all__ = [
    "DEFAULT_EVENT_REGISTRY",
    "INDUSTRY_SUBCATEGORIES",
    "INDUSTRY_TAXONOMY",
    "IndustryAnalysisResultV4",
    "RATE_DIMENSIONS",
    "RATE_LEVELS",
    "map_rate_levels",
    "validate_industry_analysis_result",
]
