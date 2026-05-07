import pymongo
from typing import Dict, List, Any


from typing import Dict, List, Any


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

    def _joined_text(self, data: Dict) -> str:
        parts = [
            data.get("EVENT_TITLE", ""),
            data.get("EVENT_BRIEF", ""),
            data.get("REASON", ""),
            data.get("TIPS", "")
        ]
        return " ".join(str(x) for x in parts if x)

    def _is_recap_like(self, data: Dict) -> bool:
        text = self._joined_text(data)
        return any(k in text for k in self.config["recap_keywords"])

    def _is_low_novelty(self, data: Dict) -> bool:
        text = self._joined_text(data)
        return any(k in text for k in self.config["low_novelty_hints"])

    def _count_sub_events(self, data: Dict) -> int:
        text = str(data.get("EVENT_TEXT", ""))
        markers = ["；", "。此外", "同时", "另一方面", "其一", "其二", "第一", "第二", "第三"]
        return sum(text.count(m) for m in markers)

    def calculate_single(
        self,
        intelligence_data: Dict,
        similar_messages: List[Dict[str, Any]] = None
    ) -> float:
        taxonomy = intelligence_data.get("TAXONOMY", "")

        if taxonomy == "无情报价值":
            return 0.0

        rates = intelligence_data.get("RATE", {})

        # 1. 加权归一化到 0-10
        raw_score = 0.0
        max_score = 0.0

        for dim, weight in self.weights.items():
            score = self._safe_rate(rates, dim)
            raw_score += score * weight
            max_score += 10.0 * weight

        if max_score <= 0:
            return 0.0

        score = raw_score / max_score * 10.0

        # 2. 分类系数
        score *= self.taxonomy_multipliers.get(taxonomy, 1.0)

        # 3. 从模型现有字段推断内容形态
        is_recap_like = self._is_recap_like(intelligence_data)
        is_low_novelty = self._is_low_novelty(intelligence_data)

        novelty = self._safe_rate(rates, "新颖性与异常性")
        actionability = self._safe_rate(rates, "可行动性")
        evolution = self._safe_rate(rates, "演化与连锁潜力")

        # 4. 相似历史消息惩罚
        similar_messages = similar_messages or []
        max_similarity = max(
            [float(x.get("similarity", 0.0)) for x in similar_messages],
            default=0.0
        )

        # 5. 回顾/总结/低新颖性惩罚
        if is_recap_like:
            score *= 0.35
            score = min(score, 3.0)

        if is_low_novelty:
            score *= 0.50
            score = min(score, 3.0)

        # 6. 高相似且低新颖性：强惩罚
        if max_similarity >= 0.86 and novelty <= 3:
            score *= 0.35
            score = min(score, 2.5)

        if max_similarity >= 0.92 and novelty <= 2:
            score *= 0.25
            score = min(score, 2.0)

        # 7. 多事件汇编惩罚
        sub_event_count = self._count_sub_events(intelligence_data)
        if sub_event_count >= 5 and novelty <= 4:
            score *= 0.55
            score = min(score, 3.5)

        # 8. 低可行动性封顶
        if actionability <= 2 and novelty <= 3:
            score = min(score, 3.0)

        # 9. 无演化、无行动、无新颖性：归档级
        if novelty <= 2 and actionability <= 2 and evolution <= 3:
            score = min(score, 2.0)

        # 10. 边界
        return round(min(10.0, max(0.0, score)), 1)
