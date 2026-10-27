"""行业领域的可选外部增强契约；当前主链不安装任何实现。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class CompanyImportance:
    """未来公司资料库返回的行业内重要性，不允许由 LLM 猜测。"""

    score: float
    industry: str
    source_version: str
    reasons: tuple[str, ...] = ()


class CompanyImportanceResolver(Protocol):
    def resolve(
        self, company_refs: Sequence[Mapping[str, Any]], *,
        industry_context: str | None, as_of: datetime | None,
    ) -> CompanyImportance | None:
        ...


@dataclass(frozen=True, slots=True)
class ArachneAssessment:
    """Arachne 对已归档事件的异步网络评估。"""

    impact_score: float
    model_version: str
    as_of: datetime
    evidence_refs: tuple[str, ...] = ()


class ArachneAssessmentProvider(Protocol):
    def get_assessment(
        self, *, domain_name: str, intelligence_uuid: str, event_id: str,
    ) -> ArachneAssessment | None:
        ...


@dataclass(frozen=True, slots=True)
class IndustryScoreContext:
    """后置重评分输入；全为空即当前第一版的纯正文评分。"""

    company_importance: CompanyImportance | None = None
    arachne_assessment: ArachneAssessment | None = None


__all__ = [
    "ArachneAssessment",
    "ArachneAssessmentProvider",
    "CompanyImportance",
    "CompanyImportanceResolver",
    "IndustryScoreContext",
]
