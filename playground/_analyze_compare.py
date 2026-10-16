"""临时脚本：深入分析 v41/v42 对比结果，区分 API 失败与校验失败，对比关键案例。"""
import json
from pathlib import Path

data = json.loads(Path('_data/prompt_compare/compare_20261012T134548Z.json').read_text(encoding='utf-8'))
results = data['results']

print('=== 失败原因分类 ===')
for e in results:
    if not e['ok']:
        err = e.get('error', '')
        kind = 'API错误' if 'unified_api_error' in err or 'HTTP' in err else '校验失败'
        print(f"  {e['uuid'][:8]} v{e['prompt_version']}: {kind}: {err[:120]}")

print('\n=== NAZA 案例 (8150b416) ===')
for e in results:
    if e['uuid'].startswith('8150b416') and e['ok']:
        ext = e['analysis']['EVENT_EXTRACTION']
        print(f"--- v{e['prompt_version']} ---")
        for ent in ext['entities']:
            print(f"  {ent['id']}: {ent['name']} ({ent['type']}, {ent.get('country_code')})")
        for ev in ext['events']:
            core = ev['core']
            print(f"  {ev['id']}: {core['predicate']} roles={core['roles']}")
            for q in ev.get('qualifiers', []):
                print(f"    qualifier: {q['type']}={q['value']} by={q.get('by')}")

print('\n=== 乌克兰发电厂案例 (b2220b56) ===')
for e in results:
    if e['uuid'].startswith('b2220b56') and e['ok']:
        ext = e['analysis']['EVENT_EXTRACTION']
        print(f"--- v{e['prompt_version']} ---")
        for ent in ext['entities']:
            print(f"  {ent['id']}: {ent['name']} ({ent['type']}, country={ent.get('country_code')})")
        for ev in ext['events']:
            core = ev['core']
            print(f"  {ev['id']}: pred={core['predicate']['id']} roles={core['roles']} context={core.get('context')}")

print('\n=== 成功案例质量指标明细 ===')
for e in results:
    if e['ok'] and e.get('quality'):
        q = e['quality']
        g = q['surface_grounding']
        print(f"  {e['uuid'][:8]} v{e['prompt_version']}: "
              f"attempts={e['attempts']} unreg={q['unregistered_role_count']} "
              f"conflict={q['role_type_conflict_count']} "
              f"pred_ground={g['predicate']['hit']}/{g['predicate']['total']} "
              f"time_ground={g['time']['hit']}/{g['time']['total']}")

