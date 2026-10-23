"""临时脚本：检查事件记录中法语原文出现的位置。"""
import json
import pymongo

db = pymongo.MongoClient('mongodb://localhost:27017')['IntelligenceIntegrationSystem']
doc = db['intelligence_v4_archived'].find_one({'UUID': 'eb5c3fd7-785c-48b6-8f8c-8bd789f2c40e'})
if not doc:
    print('archived 未找到，查 events 索引')
else:
    ext = doc.get('EVENT_EXTRACTION', {})
    print('EVENT_TITLE:', doc.get('EVENT_TITLE'))
    print('PROMPT_VERSION:', (doc.get('APPENDIX') or {}).get('__PROMPT_VERSION__'))
    for ev in ext.get('events', []):
        core = ev.get('core', {})
        print('---', ev.get('id'), 'predicate:', json.dumps(core.get('predicate'), ensure_ascii=False))
        print('    roles:', json.dumps(core.get('roles'), ensure_ascii=False))
    print('entities:', json.dumps(ext.get('entities'), ensure_ascii=False)[:600])

ev = db['intelligence_v4_events'].find_one({'intelligence_uuid': 'eb5c3fd7-785c-48b6-8f8c-8bd789f2c40e'})
if ev:
    print('\n=== v4_events 索引文档 ===')
    print(json.dumps(ev, ensure_ascii=False, default=str)[:1200])
