# demo_intelligence_query.py
import pytz
from datetime import datetime
from Tools.MongoDBAccess import MongoDBStorage
from ServiceComponent.IntelligenceQueryEngine import IntelligenceQueryEngine


def print_intel_doc(doc):
    """格式化打印情报文档"""
    print(f"UUID: {doc['UUID']}")
    print(f"时间: {', '.join(doc['TIME'])}")
    print(f"地点: {', '.join(doc['LOCATION'])}")
    print(f"人员: {', '.join(doc['PEOPLE'])}")
    print(f"组织: {', '.join(doc['ORGANIZATION'])}")
    print(f"摘要: {doc['EVENT_BRIEF']}")
    print(f"置信度: {doc['RATE'].get('confidence', 'N/A')}")
    print("-" * 50)


def demo_query_engine():
    db = MongoDBStorage(
        host="localhost",
        port=27017,
        collection_name="intelligence_archived"
    )

    # 初始化查询引擎
    engine = IntelligenceQueryEngine(db)

    # 准备查询时间范围
    start_time = datetime(2025, 1, 1, tzinfo=pytz.UTC)
    end_time = datetime(2025, 9, 30, tzinfo=pytz.UTC)

    print("=" * 70)
    print("演示1: 无限制查询 - 获取全部匹配结果")
    print("=" * 70)
    results, _ = engine.query_intelligence(
        period=(start_time, end_time),
        locations=["美国"]
    )
    print(f"找到 {len(results)} 条匹配情报")
    for doc in results[:2]:  # 仅显示前2个作为示例
        print_intel_doc(doc)

    print("\n" + "=" * 70)
    print("演示2: 使用limit=5 - 限制结果数量")
    print("=" * 70)
    results_limited, _ = engine.query_intelligence(
        period=(start_time, end_time),
        locations=["美国"],
        limit=5
    )
    print(f"找到 {len(results_limited)} 条匹配情报 (limit=5)")
    for doc in results_limited:
        print_intel_doc(doc)

    print("\n" + "=" * 70)
    print("演示3: 综合条件查询")
    print("=" * 70)
    complex_results, _ = engine.query_intelligence(
        period=(start_time, end_time),
        locations=["美国", "中国"],
        peoples=["特朗普"],
        limit=3
    )
    print(f"找到 {len(complex_results)} 条匹配情报 (limit=3)")
    for doc in complex_results:
        print_intel_doc(doc)

    print("\n" + "=" * 70)
    print("演示4: 按UUID获取单个情报")
    print("=" * 70)
    # 使用之前查询中获取的UUID进行测试
    if results:
        uuid = results[0]["UUID"]
        single_doc = engine.get_intelligence(uuid)
        if single_doc:
            print(f"找到UUID={uuid}的情报:")
            print_intel_doc(single_doc)
        else:
            print(f"未找到UUID={uuid}的情报")
    else:
        print("没有可用的UUID进行测试")


if __name__ == "__main__":
    demo_query_engine()
