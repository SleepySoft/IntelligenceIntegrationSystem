from typing import Dict


class IntelligenceScoringEngine:
    def __init__(self, config: Dict = None):
        self.config = config or self._get_default_config()
        self.weights = self.config["weights"]
        self.taxonomy_multipliers = self.config["taxonomy_multipliers"]

    def _get_default_config(self) -> Dict:
        return {
            "weights": {
                "影响深度": 3.0,
                "新颖性与异常性": 2.5,
                "演化与连锁潜力": 2.0,
                "影响广度": 1.5,
                "可行动性": 1.5,
                "舆情及认知影响": 0.5
            },
            "taxonomy_multipliers": {
                "政治与安全": 1.15,
                "经济与金融": 1.05,
                "科技与网络": 1.00,
                "社会与环境": 0.95,
                "无情报价值": 0.00
            },
            "recap_keywords": [
                "回顾", "复盘", "盘点", "总结", "综述", "周报", "月报", "年报",
                "年度", "季度", "一周", "本周", "上周", "要闻", "汇总", "合集",
                "时间线", "梳理", "十大", "观察", "回看", "进展汇编"
            ],
            "low_novelty_hints": [
                "新增事实有限", "无新增事实", "旧闻", "重复报道", "相似消息已覆盖",
                "总结类", "回顾类", "汇编类", "复述", "背景梳理"
            ]
        }

    def _safe_rate(self, rates: Dict, key: str) -> float:
        value = rates.get(key, 0)
        try:
            value = float(value)
        except Exception:
            value = 0.0
        return min(10.0, max(0.0, value))

    def calculate_v4(self, intelligence_data) -> float:
        """按 V4 嵌套结构评分，不要求调用方重新拼装 v2 归档字典。"""
        if hasattr(intelligence_data, "model_dump"):
            data = intelligence_data.model_dump(by_alias=True)
        else:
            data = dict(intelligence_data)
        message = data.get("message", {})
        classification = data.get("classification", {})
        assessment = data.get("assessment", {})
        rates = assessment.get("rate", {})

        raw_score = 0.0
        max_score = 0.0
        for dimension, weight in self.weights.items():
            raw_score += self._safe_rate(rates, dimension) * weight
            max_score += 10.0 * weight
        if max_score <= 0:
            return 0.0

        score = raw_score / max_score * 10.0
        score *= self.taxonomy_multipliers.get(classification.get("taxonomy", ""), 1.0)
        joined_text = " ".join(str(value) for value in (
            message.get("title", ""), message.get("brief", ""),
            assessment.get("reason", ""), assessment.get("tips", ""),
        ) if value)
        is_recap_like = any(key in joined_text for key in self.config["recap_keywords"])
        is_low_novelty = any(key in joined_text for key in self.config["low_novelty_hints"])
        novelty = self._safe_rate(rates, "新颖性与异常性")
        actionability = self._safe_rate(rates, "可行动性")
        evolution = self._safe_rate(rates, "演化与连锁潜力")

        if is_recap_like:
            score = min(score * 0.35, 3.0)
        if is_low_novelty:
            score = min(score * 0.50, 3.0)
        sub_event_count = sum(str(message.get("text", "")).count(marker) for marker in (
            "；", "。此外", "同时", "另一方面", "其一", "其二", "第一", "第二", "第三",
        ))
        if sub_event_count >= 5 and novelty <= 4:
            score = min(score * 0.55, 3.5)
        if actionability <= 2 and novelty <= 3:
            score = min(score, 3.0)
        if novelty <= 2 and actionability <= 2 and evolution <= 3:
            score = min(score, 2.0)
        return round(min(10.0, max(0.0, score)), 1)
