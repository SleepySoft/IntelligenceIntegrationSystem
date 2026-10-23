# VectorIntelligence/__init__.py
# 向量情报子系统：索引扩展、向量检索、聚合与动态图谱。
# 注意：装配入口刻意放在 iis_integration.py（同 SituationViews 惯例），此处不导出。

from VectorIntelligence.aggregation import (
    AggregationPlanSpec,
    IntelligenceAggregationEngine,
    generate_aggregation_plan,
)
from VectorIntelligence.extension import VectorIntelligenceExtension
from VectorIntelligence.graph import DynamicGraphEngine
from VectorIntelligence.vector_engine import IntelligenceVectorDBEngine

__all__ = [
    "AggregationPlanSpec",
    "DynamicGraphEngine",
    "IntelligenceAggregationEngine",
    "IntelligenceVectorDBEngine",
    "VectorIntelligenceExtension",
    "generate_aggregation_plan",
]
