# Event Core Engine

独立于 IIS 的 Event V4 事件管理核心。

## 设计结论

- AI 分析输出为 `ValuableIntelligenceV4`。
- 持久化时先为其中每个 Event 分配全局 UUID，并独立存储。
- `ValuableIntelligenceV4.EVENTS` 在存储模型中转换为 Event UUID 列表。
- Event 保存 `intelligence_uuid`，用于追溯所属情报，但引擎不读取或理解 `ValuableIntelligenceV4`。
- 引擎仅接受 Event、EventQuery、CanonicalEvent 等独立领域对象。
- 三层为：纯内存分析层、用例整合层、查询适配层。

完整设计见 `DESIGN.md`。

## 安装与测试

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

MongoDB 支持：

```bash
python -m pip install -e '.[mongodb]'
```

运行示例：

```bash
python examples/basic_usage.py
```
