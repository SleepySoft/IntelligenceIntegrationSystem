from ServiceComponent.IntelligenceQueryEngine import IntelligenceQueryEngine


def test_build_dynamic_conditions():
    """Test the build_dynamic_conditions function using assert statements."""
    # 1. 空条件测试
    result = IntelligenceQueryEngine.build_dynamic_conditions({})
    assert result == {}, "空条件应返回空字典"

    # 2. 单一条件测试
    result = IntelligenceQueryEngine.build_dynamic_conditions({"status": "active"})
    assert result == {"status": "active"}, "单一条件应直接返回该条件"

    # 3. 多条件AND测试
    result = IntelligenceQueryEngine.build_dynamic_conditions(
        {"status": "active", "rating": {"$gte": 4}},
        operator="$and"
    )
    expected = {"$and": [{"status": "active"}, {"rating": {"$gte": 4}}]}
    assert result == expected, "多条件AND组合错误"

    # 4. 多条件OR测试
    result = IntelligenceQueryEngine.build_dynamic_conditions(
        {"status": "pending", "priority": "high"},
        operator="$or"
    )
    expected = {"$or": [{"status": "pending"}, {"priority": "high"}]}
    assert result == expected, "多条件OR组合错误"

    # 5. 嵌套字段测试
    result = IntelligenceQueryEngine.build_dynamic_conditions({"author.name": "John"})
    assert result == {"author": {"name": "John"}}, "嵌套字段转换错误"

    # 6. 混合嵌套与普通字段
    result = IntelligenceQueryEngine.build_dynamic_conditions({
        "meta.version": 2,
        "tags": {"$in": ["urgent"]}
    })
    expected = {
        "$and": [
            {"meta": {"version": 2}},
            {"tags": {"$in": ["urgent"]}}
        ]
    }
    assert result == expected, "混合字段处理错误"

    # 7. 复杂操作符测试
    result = IntelligenceQueryEngine.build_dynamic_conditions({
        "timestamp": {"$gte": "2025-01-01", "$lte": "2025-12-31"},
        "deleted": {"$exists": False}
    })
    expected = {
        "$and": [
            {"timestamp": {"$gte": "2025-01-01", "$lte": "2025-12-31"}},
            {"deleted": {"$exists": False}}
        ]
    }
    assert result == expected, "复杂操作符处理错误"

    # 8. 无效操作符测试
    try:
        IntelligenceQueryEngine.build_dynamic_conditions({"status": "active"}, operator="$invalid")
        assert False, "无效操作符应触发ValueError"
    except ValueError as e:
        assert str(e) == "Operator must be '$and' or '$or'", "异常消息不匹配"

    # 9. 边界值测试
    # 9.1 空字符串字段名
    result = IntelligenceQueryEngine.build_dynamic_conditions({"": "value"})
    assert result == {"": "value"}, "空字符串字段名处理错误"

    # 9.2 None值条件
    result = IntelligenceQueryEngine.build_dynamic_conditions({"archived": None})
    assert result == {"archived": None}, "None值条件处理错误"

    print("✅ 所有测试通过")


def main():
    test_build_dynamic_conditions()


if __name__ == '__main__':
    main()
