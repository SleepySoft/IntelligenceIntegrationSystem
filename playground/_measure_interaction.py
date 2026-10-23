"""临时脚本：测量 v44 单次分析交互的实际收发大小。"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_V44

prompt_chars = len(EVENT_ANALYSIS_PROMPT_V44)
print(f'v44 prompt: {prompt_chars} 字符')

# 近期生产调用的 prompt token 数（含正文），来自限速器 sqlite
conn = sqlite3.connect('_data/ai_rate_limits.sqlite3')
rows = conn.execute(
    "SELECT prompt_tokens FROM rate_limit_usage ORDER BY requested_at DESC LIMIT 200"
).fetchall()
tokens = sorted(r[0] for r in rows if r[0])
if tokens:
    n = len(tokens)
    print(f'近期 {n} 次调用 prompt_tokens: '
          f'min={tokens[0]} 中位={tokens[n // 2]} p90={tokens[int(n * 0.9)]} max={tokens[-1]}')

# 正文大小分布
import pymongo
db = pymongo.MongoClient('mongodb://localhost:27017')['IntelligenceIntegrationSystem']
docs = list(db['intelligence_v4_archived'].find(
    {'APPENDIX.__PROMPT_VERSION__': {'$gte': 42}},
    {'APPENDIX.RAW_DATA.content': 1}).sort('APPENDIX.__TIME_ARCHIVED__', -1).limit(100))
lengths = sorted(len((d.get('APPENDIX', {}).get('RAW_DATA') or {}).get('content', '') or '')
                 for d in docs)
if lengths:
    n = len(lengths)
    print(f'近 {n} 篇正文: min={lengths[0]} 中位={lengths[n // 2]} '
          f'p90={lengths[int(n * 0.9)]} max={lengths[-1]} 字符')

# 响应大小：用最近一次 v44 冒烟的分析结果估算
import json, glob
files = sorted(glob.glob('_data/prompt_compare/compare_*.json'))
for f in files[-2:]:
    data = json.load(open(f, encoding='utf-8'))
    for e in data['results']:
        if e.get('ok') and e.get('analysis'):
            raw = json.dumps(e['analysis'], ensure_ascii=False)
            print(f"{Path(f).name} {e['uuid'][:8]} v{e['prompt_version']}: "
                  f"响应 JSON {len(raw)} 字符（约 {len(raw)//2}-{len(raw)} tokens）")
