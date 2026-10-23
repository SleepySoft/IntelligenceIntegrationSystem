# ServiceComponent/adapters/iis_vector.py
# 兼容壳：实现已迁至 VectorIntelligence/extension.py，本文件仅 re-export。
# 新代码请直接引用 VectorIntelligence.extension。

from VectorIntelligence.extension import (
    IISVectorExtension,
    VectorIntelligenceExtension,
)

__all__ = ["IISVectorExtension", "VectorIntelligenceExtension"]
