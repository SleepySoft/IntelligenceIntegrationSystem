# ServiceComponent/DynamicGraphEngine.py
# 兼容壳：实现已迁至 VectorIntelligence/graph.py，本文件仅 re-export。
# 新代码请直接引用 VectorIntelligence.graph。

from VectorIntelligence.graph import (
    DynamicGraphEngine,
    GraphEdge,
    GraphNode,
    StorylineSnapshot,
    SubNode,
)

__all__ = [
    "DynamicGraphEngine",
    "GraphEdge",
    "GraphNode",
    "StorylineSnapshot",
    "SubNode",
]
