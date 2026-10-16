"""按 __PROMPT_VERSION__ 分组评估 v42 vs v43 生产归档质量。"""
import json
from collections import Counter, defaultdict

import pymongo

db = pymongo.MongoClient('mongodb://localhost:27017')['IntelligenceIntegrationSystem']
coll = db['intelligence_v4_archived']

by_version = defaultdict(list)
for doc in coll.find({}, {'EVENT_EXTRACTION': 1, 'RATE': 1, 'APPENDIX': 1,
                          'TIME': 1, 'LOCATION': 1}):
    version = (doc.get('APPENDIX') or {}).get('__PROMPT_VERSION__')
    by_version[version].append(doc)

print('各版本归档数量:', {v: len(d) for v, d in sorted(by_version.items(), key=lambda x: str(x[0]))})


def quality_agg(docs):
    q_list = [(d.get('APPENDIX') or {}).get('__EXTRACTION_QUALITY__') for d in docs]
    q_list = [q for q in q_list if q]
    if not q_list:
        return None
    pred_hit = sum(q['surface_grounding']['predicate']['hit'] for q in q_list)
    pred_total = sum(q['surface_grounding']['predicate']['total'] for q in q_list)
    time_hit = sum(q['surface_grounding']['time']['hit'] for q in q_list)
    time_total = sum(q['surface_grounding']['time']['total'] for q in q_list)
    return {
        'coverage': f"{len(q_list)}/{len(docs)}",
        'unregistered_roles': sum(q['unregistered_role_count'] for q in q_list),
        'role_type_conflicts': sum(q['role_type_conflict_count'] for q in q_list),
        'predicate_grounding': f"{pred_hit}/{pred_total}",
        'time_grounding': f"{time_hit}/{time_total}",
    }


def extraction_agg(docs):
    stats = Counter()
    events_per, entities_per = [], []
    for d in docs:
        ext = d.get('EVENT_EXTRACTION') or {}
        events = ext.get('events') or []
        entities = ext.get('entities') or []
        events_per.append(len(events))
        entities_per.append(len(entities))
        stats['records'] += 1
        if any(e.get('qualifiers') for e in events):
            stats['with_qualifier'] += 1
        if any((e.get('core') or {}).get('context') for e in events):
            stats['with_context'] += 1
        if any((e.get('core') or {}).get('time') for e in events):
            stats['with_time'] += 1
        if any(e.get('relations') for e in events):
            stats['with_relation'] += 1
        # 物理实体国别标注率
        physical = [e for e in entities if e.get('type') in
                    ('facility', 'location', 'equipment', 'asset', 'resource')]
        if physical:
            stats['physical_entities'] += len(physical)
            stats['physical_with_cc'] += sum(1 for e in physical if e.get('country_code'))
        # 计划/指令/epistemic 限定词细分
        for e in events:
            for q in e.get('qualifiers') or []:
                stats[f"q_{q.get('type')}"] += 1
    n = max(stats['records'], 1)
    out = {
        'events_per_record': round(sum(events_per) / n, 2),
        'entities_per_record': round(sum(entities_per) / n, 2),
        'with_qualifier%': round(100 * stats['with_qualifier'] / n, 1),
        'with_context%': round(100 * stats['with_context'] / n, 1),
        'with_time%': round(100 * stats['with_time'] / n, 1),
        'with_relation%': round(100 * stats['with_relation'] / n, 1),
    }
    if stats['physical_entities']:
        out['physical_cc%'] = round(
            100 * stats['physical_with_cc'] / stats['physical_entities'], 1)
    for key in sorted(stats):
        if key.startswith('q_'):
            out[key] = stats[key]
    return out


def rate_agg(docs):
    dims = defaultdict(list)
    scores = []
    for d in docs:
        rate = d.get('RATE') or {}
        for k, v in rate.items():
            dims[k].append(v)
        total = (d.get('APPENDIX') or {}).get('__TOTAL_SCORE__')
        if total is not None:
            scores.append(total)
    out = {'score_avg': round(sum(scores) / len(scores), 2) if scores else None,
           'score_dist': dict(sorted(Counter(round(s) for s in scores).items()))}
    for k, values in dims.items():
        out[k] = dict(sorted(Counter(values).items()))
    return out


for version in sorted(by_version, key=str):
    docs = by_version[version]
    print(f'\n{"=" * 60}\nPrompt v{version}（{len(docs)} 条）')
    print('质量检查:', json.dumps(quality_agg(docs), ensure_ascii=False))
    print('抽取结构:', json.dumps(extraction_agg(docs), ensure_ascii=False))
    print('评分分布:', json.dumps(rate_agg(docs), ensure_ascii=False))
