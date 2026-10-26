"""兼容导入壳；新代码从公共采集契约或具体领域目录导入。"""

from ServiceComponent.collected_data import CollectedData
from IntelligenceDomains.news.schema import (
    AnalysisResultV4,
    DEFAULT_EVENT_REGISTRY,
    NON_INTELLIGENCE_TAXONOMY,
    RATE_DIMENSIONS,
    RATE_LEVELS,
    TAXONOMY_SUBCATEGORIES,
    event_v4_json_schema,
    map_rate_levels,
    validate_analysis_result_v4,
)

__all__ = [
    "AnalysisResultV4", "CollectedData", "DEFAULT_EVENT_REGISTRY",
    "NON_INTELLIGENCE_TAXONOMY", "RATE_DIMENSIONS", "RATE_LEVELS",
    "TAXONOMY_SUBCATEGORIES", "event_v4_json_schema", "map_rate_levels",
    "validate_analysis_result_v4",
]
