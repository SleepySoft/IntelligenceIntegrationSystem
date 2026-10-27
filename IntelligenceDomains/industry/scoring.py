"""行业情报双轨评分：决策价值为主，商业故事性提供受控提升。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .enrichment import IndustryScoreContext


SCORING_VERSION = "industry/1.0"


@dataclass(frozen=True, slots=True)
class IndustryScoreBreakdown:
    value_score: float
    interest_score: float
    base_score: float
    company_importance_bonus: float
    arachne_network_bonus: float
    total_score: float
    scoring_version: str = SCORING_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class IndustryScoringEngine:
    """不依赖公司库和 Arachne；两者仅在显式传入上下文时参与后置重评分。"""

    DEFAULT_VALUE_WEIGHTS = {
        "行动实质性": 0.20,
        "规模与力度": 0.10,
        "事实确定性": 0.10,
        "认知增量": 0.20,
        "预期差与异常性": 0.10,
        "经营与战略影响": 0.15,
        "投资可用性": 0.15,
    }
    DEFAULT_INTEREST_WEIGHTS = {
        "关注度与故事性": 0.50,
        "预期差与异常性": 0.30,
        "认知增量": 0.20,
    }

    def __init__(self, config: Mapping[str, Any] | None = None):
        config = dict(config or {})
        self.value_weights = self._weights(
            self.DEFAULT_VALUE_WEIGHTS, config.get("value_weights"))
        self.interest_weights = self._weights(
            self.DEFAULT_INTEREST_WEIGHTS, config.get("interest_weights"))
        self.interest_mix = self._bounded(config.get("interest_mix", 0.40), 0.0, 0.50)
        self.max_company_bonus = self._bounded(
            config.get("max_company_bonus", 1.0), 0.0, 2.0)
        self.max_arachne_bonus = self._bounded(
            config.get("max_arachne_bonus", 1.0), 0.0, 2.0)

    @staticmethod
    def _bounded(value: Any, minimum: float, maximum: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = minimum
        return min(maximum, max(minimum, number))

    @classmethod
    def _weights(cls, defaults: Mapping[str, float], override: Any) -> dict[str, float]:
        weights = dict(defaults)
        if isinstance(override, Mapping):
            for name in defaults:
                if name in override:
                    weights[name] = cls._bounded(override[name], 0.0, 100.0)
        total = sum(weights.values())
        if total <= 0:
            raise ValueError("industry scoring weights must contain a positive value")
        return {name: value / total for name, value in weights.items()}

    @staticmethod
    def _rates(result: Any) -> Mapping[str, Any]:
        if hasattr(result, "RATE"):
            return result.RATE
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="python").get("RATE", {})
        return dict(result).get("RATE", {})

    @classmethod
    def _rate(cls, rates: Mapping[str, Any], name: str) -> float:
        return cls._bounded(rates.get(name, 0), 0.0, 10.0)

    def _weighted(self, rates: Mapping[str, Any], weights: Mapping[str, float]) -> float:
        return sum(self._rate(rates, name) * weight for name, weight in weights.items())

    def calculate_breakdown(
        self, result: Any, context: IndustryScoreContext | None = None,
    ) -> IndustryScoreBreakdown:
        rates = self._rates(result)
        value_score = self._weighted(rates, self.value_weights)
        interest_score = self._weighted(rates, self.interest_weights)
        mixed = value_score * (1.0 - self.interest_mix) + interest_score * self.interest_mix
        base_score = max(value_score, mixed)

        materiality = self._rate(rates, "行动实质性")
        certainty = self._rate(rates, "事实确定性")
        novelty = self._rate(rates, "认知增量")

        # 故事性只能抬升已有经营事实，不能把营销或猎奇内容变成高价值情报。
        if materiality <= 2:
            base_score = min(base_score, 4.0)
        if novelty <= 2 and materiality <= 3:
            base_score = min(base_score, 4.0)
        # 未具名传闻和单一间接来源保留观察价值，但限制最终确定性。
        if certainty <= 1:
            base_score = min(base_score, 3.0)
        elif certainty <= 2:
            base_score = min(base_score, 5.0)
        elif certainty <= 4:
            base_score = min(base_score, 6.5)

        company_bonus = 0.0
        arachne_bonus = 0.0
        if context is not None and materiality >= 5:
            if context.company_importance is not None:
                normalized = self._bounded(context.company_importance.score, 0.0, 10.0) / 10.0
                company_bonus = self.max_company_bonus * normalized
            if context.arachne_assessment is not None:
                normalized = self._bounded(context.arachne_assessment.impact_score, 0.0, 10.0) / 10.0
                arachne_bonus = self.max_arachne_bonus * normalized

        total = min(10.0, base_score + company_bonus + arachne_bonus)
        return IndustryScoreBreakdown(
            value_score=round(value_score, 2),
            interest_score=round(interest_score, 2),
            base_score=round(base_score, 2),
            company_importance_bonus=round(company_bonus, 2),
            arachne_network_bonus=round(arachne_bonus, 2),
            total_score=round(total, 1),
        )

    def calculate(self, result: Any) -> float:
        return self.calculate_breakdown(result).total_score

    def calculate_v4(self, result: Any) -> float:
        """兼容旧评分器调用名。"""
        return self.calculate(result)


__all__ = [
    "IndustryScoreBreakdown",
    "IndustryScoringEngine",
    "SCORING_VERSION",
]
