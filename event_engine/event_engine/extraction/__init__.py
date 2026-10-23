"""Event Engine 的独立事件抽取契约与 Prompt。"""

from .models import (
    EventExtractionResult,
    ExtractedEntity,
    ExtractedEvent,
    event_extraction_json_schema,
    validate_event_extraction,
)
from .prompt import (
    build_compact_predicate_catalog,
    build_event_extraction_prompt,
    build_event_extraction_section,
    build_predicate_catalog,
)
from .compact_prompt import build_compact_core_extraction_section
from .subset import (
    CoreExtractionSubset,
    SubsetEntity,
    SubsetEvent,
    complete_core_extraction,
)
from .subset_prompt import (
    CORE_ROLE_LANDING_SECTION,
    FULL_TIME_RULE,
    MINIMAL_TIME_RULE,
    build_core_extraction_prompt,
    build_core_extraction_section,
)

__all__ = [
    "EventExtractionResult",
    "ExtractedEntity",
    "ExtractedEvent",
    "event_extraction_json_schema",
    "validate_event_extraction",
    "build_event_extraction_prompt",
    "build_event_extraction_section",
    "build_predicate_catalog",
    "build_compact_predicate_catalog",
    "CORE_ROLE_LANDING_SECTION",
    "FULL_TIME_RULE",
    "MINIMAL_TIME_RULE",
    "build_compact_core_extraction_section",
    "CoreExtractionSubset",
    "SubsetEntity",
    "SubsetEvent",
    "complete_core_extraction",
    "build_core_extraction_prompt",
    "build_core_extraction_section",
]
