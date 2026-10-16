# intelligence_query_engine_test_demo.py
import pytz
from datetime import datetime, timedelta
from Tools.MongoDBAccess import MongoDBStorage
from ServiceComponent.IntelligenceQueryEngine import IntelligenceQueryEngine


def populate_test_data(collection):
    """填充测试数据到数据库"""
    # 清空现有数据
    collection.delete_many({})

    # 生成测试数据
    test_data = []
    base_time = datetime.now(pytz.utc)
    for i in range(1, 21):
        test_data.append({
            "UUID": f"uuid_{i:02d}",
            "TIME": (base_time - timedelta(days=i)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            "LOCATION": ["Area_A"] if i <= 10 else ["Area_B"],
            "PEOPLE": [f"Person_{i // 5}"],
            "ORGANIZATION": ["Org_X"],
            "EVENT_BRIEF": f"Event {i}",
            "EVENT_TEXT": f"Detailed description of event {i}",
            "RATE": {"confidence": 0.8},
            "IMPACT": "Medium",
            "TIPS": f"Tip for event {i}"
        })

    # 插入测试数据
    collection.insert_many(test_data)
    print(f"插入 {len(test_data)} 条测试数据")


def test_get_intelligence_summary(engine):
    """测试情报摘要函数"""
    print("\n=== 测试 get_intelligence_summary ===")
    result = engine.get_intelligence_summary()

    print(f"返回结果: total_count={result['total_count']}, base_uuid={result['base_uuid']}")
    print(f"预期: total_count 应为 20, base_uuid 应为最新文档的UUID")

    if result['total_count'] == 20:
        print("✅ total_count 验证成功")
    else:
        print(f"❌ total_count 验证失败 (预期: 20, 实际: {result['total_count']})")

    if result['base_uuid'] == "uuid_01":
        print("✅ base_uuid 验证成功")
    else:
        print(f"❌ base_uuid 验证失败 (预期: uuid_01, 实际: {result['base_uuid']})")

    return result['base_uuid']


def test_get_paginated_intelligences(engine, base_uuid):
    """测试分页查询函数"""
    print("\n=== 测试 get_paginated_intelligences ===")

    # 测试场景1: 获取第一页数据
    print("\n场景1: 获取第一页 (offset=0, limit=5)")
    page1 = engine.get_paginated_intelligences(base_uuid, offset=0, limit=5)
    print(f"返回 {len(page1)} 条记录")
    print("UUID列表:", [doc['UUID'] for doc in page1])
    print("预期: 应返回最新的5条记录 (uuid_01 到 uuid_05)")

    # 测试场景2: 获取第二页数据
    print("\n场景2: 获取第二页 (offset=5, limit=5)")
    page2 = engine.get_paginated_intelligences(base_uuid, offset=5, limit=5)
    print(f"返回 {len(page2)} 条记录")
    print("UUID列表:", [doc['UUID'] for doc in page2])
    print("预期: 应返回 uuid_06 到 uuid_10")

    # 测试场景3: 超出范围的offset
    print("\n场景3: 超出范围的offset (offset=100, limit=5)")
    page3 = engine.get_paginated_intelligences(base_uuid, offset=100, limit=5)
    print(f"返回 {len(page3)} 条记录")
    print("预期: 应返回空列表")

    # 测试场景4: 验证排序稳定性
    print("\n场景4: 验证排序稳定性")
    print("获取第一页后插入新文档，再次获取第二页应保持相同结果")

    # 插入新文档
    new_doc = {
        "UUID": "uuid_new",
        "TIME": datetime.now(pytz.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        "LOCATION": ["Area_New"],
        "PEOPLE": ["New_Person"],
        "ORGANIZATION": ["Org_New"],
        "EVENT_BRIEF": "New event",
        "EVENT_TEXT": "New event description",
        "RATE": {"confidence": 0.9},
        "IMPACT": "High",
        "TIPS": "New tip"
    }
    engine._IntelligenceQueryEngine__mongo_db.collection.insert_one(new_doc)
    print("已插入新文档: uuid_new")

    # 再次获取第二页
    page2_after_insert = engine.get_paginated_intelligences(base_uuid, offset=5, limit=5)
    print(f"返回 {len(page2_after_insert)} 条记录")
    print("UUID列表:", [doc['UUID'] for doc in page2_after_insert])
    print("预期: 应返回 uuid_06 到 uuid_10 (不受新文档影响)")


def main():
    """主测试函数"""
    # 初始化数据库连接
    db = MongoDBStorage(db_name = 'test_db',)
    engine = IntelligenceQueryEngine(db)

    # 填充测试数据
    populate_test_data(db.collection)

    # 执行测试
    base_uuid = test_get_intelligence_summary(engine)
    test_get_paginated_intelligences(engine, base_uuid)

    # 清理测试数据
    db.collection.delete_many({})
    print("\n测试数据已清理")


if __name__ == "__main__":
    main()
