"""临时脚本：统计归档记录中 surface 字段与原文的逐字命中率。"""
import re
import pymongo

db = pymongo.MongoClient('mongodb://localhost:27017', serverSelectionTimeoutMS=3000)['IntelligenceIntegrationSystem']
coll = db['intelligence_v4_archived']
docs = list(coll.find({'APPENDIX.__TOTAL_SCORE__': {'$gt': 6}}).sort('APPENDIX.__TIME_ARCHIVED__', -1).limit(15))
print('样本数:', len(docs))


def norm(s):
    return re.sub(r'\s+', '', str(s)).casefold()


stats = {'predicate': [0, 0], 'time': [0, 0], 'qualifier': [0, 0]}
misses = []
for d in docs:
    raw = (d.get('APPENDIX') or {}).get('RAW_DATA') or {}
    haystack = norm(raw.get('content', '')) + norm(raw.get('title', ''))
    lang_hint = norm(raw.get('title', ''))[:40]
    ext = d.get('EVENT_EXTRACTION') or {}
    for ev in ext.get('events', []):
        core = ev.get('core', {})
        pred = core.get('predicate', {})
        s = pred.get('surface')
        if s:
            stats['predicate'][0] += 1
            if norm(s) in haystack:
                stats['predicate'][1] += 1
            else:
                misses.append(('pred', pred.get('id'), s, lang_hint))
        t = core.get('time') or {}
        for slot, expr in t.items():
            s = (expr or {}).get('surface')
            if s:
                stats['time'][0] += 1
                if norm(s) in haystack:
                    stats['time'][1] += 1
                else:
                    misses.append(('time', slot, s, lang_hint))
        for q in ev.get('qualifiers', []):
            s = q.get('surface')
            if s:
                stats['qualifier'][0] += 1
                if norm(s) in haystack:
                    stats['qualifier'][1] += 1
                else:
                    misses.append(('qual', q.get('type'), s, lang_hint))

for kind, (tot, hit) in stats.items():
    print(f'{kind}.surface 逐字命中: {hit}/{tot}' + (f' ({100*hit/tot:.0f}%)' if tot else ''))
print('未命中样本:')
for m in misses[:10]:
    print('  ', m)

