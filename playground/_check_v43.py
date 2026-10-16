import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prompts_event_v4 import (
    EVENT_V4_PROMPT_CATALOG, EVENT_ANALYSIS_PROMPT_V43, PROMPT_EXTRACTION_MODES,
)
print('sampling:', EVENT_V4_PROMPT_CATALOG.sampling_versions())
print('selectable:', EVENT_V4_PROMPT_CATALOG.selectable_versions())
print('modes:', PROMPT_EXTRACTION_MODES)
assert '影响广度：unknown' in EVENT_ANALYSIS_PROMPT_V43
assert '"local"' in EVENT_ANALYSIS_PROMPT_V43
assert '1至10整数' not in EVENT_ANALYSIS_PROMPT_V43
assert '最高选第 2 级' in EVENT_ANALYSIS_PROMPT_V43
print('V43 prompt OK, len:', len(EVENT_ANALYSIS_PROMPT_V43))
