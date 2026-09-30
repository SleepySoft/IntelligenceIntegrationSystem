# Event Core Engine

独立于 IIS 的 Event V4 事件管理核心。

## EventIR、通用内核与领域配置

- `event_engine.ir`：纯事件语义（EventIR、Frame、角色、时间、属性、Qualifier、Relation），无新闻来源或存储依赖。
- `event_engine.core`：观察记录、查询与 Repository 协议、配置 Registry、分析、CanonicalEvent 匹配和用例编排。
- `event_engine.configs.news`：情报（新闻）的通用报道词汇。
- `event_engine.configs.industry`：生产、建设、供应等产业词汇。
- `event_engine.configs.financial`：交易、融资、债券发行、评级等金融词汇。
- `event_engine.extensions.news`：战争区域等新闻领域分析。
- `event_engine.query`：内存与 MongoDB 存储适配器。

三个配置包按词汇维护职责划分，可以组合加载；新闻报道产业或金融事件时同时加载相应包。
旧谓词 ID 不变，跨领域使用同一份定义，不重复注册同名谓词。
新增产业、金融专用谓词只提供首批示例定义，尚不是完整领域词典。
配置既可约束外部输入，也提供内部角色投影和身份比较规则；内核不按领域名称分支。

```python
from event_engine.core import EventEngine, PredicateRegistry
from event_engine.configs import INDUSTRY_PACK, FINANCIAL_PACK
from event_engine.query.memory import InMemoryEventRepository

registry = PredicateRegistry.from_packs(INDUSTRY_PACK, FINANCIAL_PACK)
engine = EventEngine(InMemoryEventRepository(), registry=registry)
```

新核心 API 默认使用空 Registry；`integration.service.EventEngine` 兼容入口默认组合三个包。
接入、分析、匹配共享同一 Registry，显式传入空配置不会重新启用默认词汇。
`EventRecord.ir` 提取纯语义，`EventRecord.from_ir(...)` 为语义补充来源与存储身份。
本阶段保留 EventRecord 的旧构造参数及存储格式，旧 `domain`、`analysis` 导入仍可用。

本次完成结构抽取和统一配置注入；CanonicalEvent 身份属性比较、完整生命周期校验、
跨来源事实性裁决仍沿用原薄版实现，不能将 `current_qualifiers` 视为已核实的现实状态。

## 设计结论

- AI 分析输出为 `ValuableIntelligenceV4`。
- 持久化时先为其中每个 Event 分配全局 UUID，并独立存储。
- `ValuableIntelligenceV4.EVENTS` 在存储模型中转换为 Event UUID 列表。
- Event 保存 `intelligence_uuid`，用于追溯所属情报，但引擎不读取或理解 `ValuableIntelligenceV4`。
- 引擎仅接受 Event、EventQuery、CanonicalEvent 等独立领域对象。
- 三层为：纯内存分析层、用例整合层、查询适配层。

完整设计见 `DESIGN.md`。

面向情报分析的长期能力、优先级和实施阶段见
[`INTELLIGENCE_ANALYSIS_ROADMAP.md`](INTELLIGENCE_ANALYSIS_ROADMAP.md)。

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
