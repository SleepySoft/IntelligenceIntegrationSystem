"""临时脚本：查看归档文档顶层与 APPENDIX 的键，寻找原文内容。"""
import pymongo

db = pymongo.MongoClient('mongodb://localhost:27017', serverSelectionTimeoutMS=3000)['IntelligenceIntegrationSystem']
coll = db['intelligence_v4_archived']
d = coll.find_one({'APPENDIX.__TOTAL_SCORE__': {'$gt': 6}})
print('top keys:', sorted(d.keys()))
print('APPENDIX keys:', sorted((d.get('APPENDIX') or {}).keys()))
print('EVENT_TEXT:', str(d.get('EVENT_TEXT'))[:120])
# 看看 cache 集合里有没有原文
names = [n for n in db.list_collection_names() if 'cache' in n.lower() or 'v4' in n.lower()]
print('collections:', names)
