from pymongo import MongoClient


APPENDIX_MAX_RATE_CLASS = '__MAX_RATE_CLASS__'
APPENDIX_MAX_RATE_SCORE = '__MAX_RATE_SCORE__'
APPENDIX_MAX_RATE_CLASS_EXCLUDE = '内容准确率'


def find_and_update_max_rate(collection_name):
    # 连接到MongoDB
    client = MongoClient('localhost', 27017, tz_aware=True)
    db = client['IntelligenceIntegrationSystem']  # 替换为实际数据库名
    collection = db[collection_name]

    # 遍历所有文档
    for document in collection.find():
        if 'RATE' not in document or not isinstance(document['RATE'], dict):
            continue

        rates = document['RATE']
        # 排除特定字段
        if APPENDIX_MAX_RATE_CLASS_EXCLUDE in rates:
            del rates[APPENDIX_MAX_RATE_CLASS_EXCLUDE]

        if not rates:
            continue

        # 找到最大值的键值对
        max_key = max(rates, key=rates.get)
        max_value = rates[max_key]

        # 准备更新数据
        appendix = document.get('APPENDIX', {})
        appendix.update({
            APPENDIX_MAX_RATE_CLASS: max_key,
            APPENDIX_MAX_RATE_SCORE: max_value
        })

        # 更新文档
        collection.update_one(
            {'_id': document['_id']},
            {'$set': {'APPENDIX': appendix}}
        )


if __name__ == "__main__":
    find_and_update_max_rate('intelligence_archived')  # 替换为实际集合名
