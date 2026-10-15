"""临时脚本：检查事件记录的限定词与展示相关字段。"""
import json
import pymongo

db = pymongo.MongoClient('mongodb://localhost:27017')['IntelligenceIntegrationSystem']
doc = db['intelligence_v4_archived'].find_one({'UUID': '9edaafc0-1430-4ebd-bad5-54486862d5f0'})
if not doc:
    print('记录不存在')
    raise SystemExit
print('EVENT_TITLE:', doc.get('EVENT_TITLE'))
print('PROMPT_VERSION:', (doc.get('APPENDIX') or {}).get('__PROMPT_VERSION__'))
ext = doc.get('EVENT_EXTRACTION', {})
for ev in ext.get('events', []):
    core = ev.get('core', {})
    print('---', ev.get('id'), 'predicate:', json.dumps(core.get('predicate'), ensure_ascii=False))
    print('    qualifiers:', json.dumps(ev.get('qualifiers'), ensure_ascii=False))
    print('    roles:', json.dumps(core.get('roles'), ensure_ascii=False))

ev_docs = list(db['intelligence_v4_events'].find(
    {'intelligence_uuid': '9edaafc0-1430-4ebd-bad5-54486862d5f0'}))
print('\n=== v4_events 索引（qualifiers 部分）===')
for ev in ev_docs:
    print(ev.get('local_event_id'), json.dumps(ev.get('qualifiers'), ensure_ascii=False))
