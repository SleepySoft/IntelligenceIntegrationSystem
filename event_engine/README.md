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

## 单文件接入演示

`Event File v1` 使用一个 JSON 文件保存实体表和事件表。文件内的实体、事件和情报 ID
会按 `dataset_id` 稳定映射为 UUID，因此同一个文件反复解析会得到相同标识。
演示会根据每个谓词的角色定义，分别列出主体、客体及 instrument、location、source
等其他角色。

仓库提供了一个包含 40 余个不同主题事件的数据集：

```bash
python -m examples.file_ingestion_demo
```

也可以传入自己的文件：

```bash
python -m examples.file_ingestion_demo path/to/events.json
```

示例文件可通过下列命令重新生成：

```bash
python -m examples.build_multitopic_event_file
```
