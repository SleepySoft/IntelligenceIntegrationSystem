"""临时脚本：查看指定记录的分类与依据。"""
import json
import pymongo

db = pymongo.MongoClient('mongodb://localhost:27017')['IntelligenceIntegrationSystem']
doc = db['intelligence_v4_archived'].find_one({'UUID': '5dc79e9d-cd8a-4bbd-9782-8031caf78213'})
if not doc:
    print('未找到')
    raise SystemExit
print('TITLE:', doc.get('EVENT_TITLE'))
print('TAXONOMY:', doc.get('TAXONOMY'))
print('SUB_CATEGORY:', doc.get('SUB_CATEGORY'))
print('REASON:', doc.get('REASON'))
print('EVENT_BRIEF:', doc.get('EVENT_BRIEF'))
print('RATE:', json.dumps(doc.get('RATE'), ensure_ascii=False))
print('PROMPT_VERSION:', (doc.get('APPENDIX') or {}).get('__PROMPT_VERSION__'))
print('AI_MODEL:', (doc.get('APPENDIX') or {}).get('__AI_MODEL__'))
print('INFORMANT:', doc.get('INFORMANT'))
ext = doc.get('EVENT_EXTRACTION') or {}
for ev in ext.get('events', []):
    core = ev.get('core', {})
    print('---', ev.get('id'), json.dumps(core.get('predicate'), ensure_ascii=False),
          '| roles:', json.dumps(core.get('roles'), ensure_ascii=False))
print('entities:', json.dumps(ext.get('entities'), ensure_ascii=False)[:800])
raw = (doc.get('APPENDIX') or {}).get('RAW_DATA') or {}
print('\n原标题:', raw.get('title'))
print('原正文前 600 字:', (raw.get('content') or '')[:600])
