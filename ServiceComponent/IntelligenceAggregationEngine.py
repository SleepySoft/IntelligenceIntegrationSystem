# ServiceComponent/IntelligenceAggregationEngine.py
# 兼容壳：实现已迁至 VectorIntelligence/aggregation.py，本文件仅 re-export。
# 新代码请直接引用 VectorIntelligence.aggregation。

from VectorIntelligence.aggregation import (
    AggregationPlanSpec,
    IntelligenceAggregationEngine,
    generate_aggregation_plan,
)

__all__ = [
    "AggregationPlanSpec",
    "IntelligenceAggregationEngine",
    "generate_aggregation_plan",
]
