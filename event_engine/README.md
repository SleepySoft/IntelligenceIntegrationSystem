# Event Core Engine

独立于 IIS 的 Event V4 事件管理核心。

## Schema、通用内核与领域配置

- `event_engine.schema`：公共数据与契约，包括 EventIR、观察记录、CanonicalEvent、匹配结果、状态投影、规则定义、查询和 Repository 协议；不依赖执行内核、领域配置或存储实现。
- `event_engine.core`：配置 Registry、校验、分析、CanonicalEvent 匹配、状态计算和用例编排。
- `event_engine.domains.news`：新闻领域的规则配置、专用声明和算法。
- `event_engine.domains.industry`：产业领域，目前提供生产、建设、供应等规则配置。
- `event_engine.domains.financial`：金融领域，目前提供交易、融资、债券发行、评级等规则配置。
- `event_engine.query`：内存与 MongoDB 存储适配器。

三个配置包按词汇维护职责划分，可以组合加载；新闻报道产业或金融事件时同时加载相应包。
旧谓词 ID 不变，跨领域使用同一份定义，不重复注册同名谓词。
新增产业、金融专用谓词只提供首批示例定义，尚不是完整领域词典。
配置既可约束外部输入，也提供内部角色投影和身份比较规则；内核不按领域名称分支。

```python
from event_engine.core import EventEngine
from event_engine.domains import registry_for
from event_engine.query.memory import InMemoryEventRepository

registry = registry_for("industry", "financial")
engine = EventEngine(InMemoryEventRepository(), registry=registry)
```

`domains` 入口不自动导入领域；`registry_for()` 不传参数时返回空 Registry，
指定金融时不加载新闻、产业或其算法。各领域的 `__init__` 只导出配置 `PACK`，
专用算法需显式导入，便于选择和裁剪。自定义包仍可用 `PredicateRegistry.from_packs(...)`。

```python
from event_engine.domains.news.analyzer import NewsAnalyzer
from event_engine.schema import EventQuery

engine = EventEngine(InMemoryEventRepository(), registry=registry_for("news"))
events = engine.events.search(EventQuery()).items
zones = NewsAnalyzer(engine.analyzer).extract_war_zones(events)
```

通用引擎不再提供 `extract_war_zones`；领域结果 `WarZoneView` 位于
`domains/news/schema.py`。产业、金融尚无专用分析算法，不创建空算法占位文件。

引擎 API 默认使用空 Registry；调用方显式选择领域并组装引擎，`integration/` 已移除。
接入、分析、匹配共享同一 Registry，显式传入空配置不会重新启用默认词汇。
`EventRecord.ir` 提取纯语义，`EventRecord.from_ir(...)` 为语义补充来源与存储身份。
保留 EventRecord 的旧构造参数及存储格式；`domain/`、`ir/` 和 core 下的协议文件已移除，
数据与契约统一从 `event_engine.schema` 导入，功能从 `event_engine.core` 导入。
`analysis/`、`configs/`、`extensions/` 也已移除；原配置与领域算法改从 `domains/` 导入。
原 `DEFAULT_PREDICATE_SPECS` 常量改为显式调用 `default_registry()`（全部领域）
或 `registry_for(...)`（所选领域），避免导入时自动组装所有配置。

```python
from event_engine.schema import EventRecord, IdentitySpec, EventQuery, EventRepository
from event_engine.core import EventAnalyzer, EventEngine, PredicateRegistry
```

## 身份匹配与状态整合

领域包版本为 `1.1`，所有内置谓词声明 Frame；注册和匹配均执行约束校验。
Frame 只描述结构，不据此推断所有权、收益或其它领域结论。实际字段意义由 PredicateSpec 提供。

自动合并先检查必备身份证据，再计算分数。身份角色默认必须齐全且一致；缺失、部分重叠、
粗粒度或近似的关键时间不会被其它维度的高分补偿。IdentitySpec 可配置必备角色、必备属性、
必备时间、最低证据覆盖率和时间精度。只有声明的身份属性参与匹配，非身份金额变化不改变事件身份。
不同卖方、身份属性、关键地点或超出窗口的事件时间会阻止合并；数值单位不能比较时返回未知。
区间事件比较完整端点，新成员还需与原成员兼容，不能利用聚合后的并集桥接不同事件。

```python
canonical, result = engine.resolve_canonical(observation_uuid)
if canonical is None:  # ambiguous：保留观察，未自动绑定，也未创建重复身份
    print(result.unknown, result.conflicts)
```

明确不同的事件创建新 CanonicalEvent；重复解析同一观察返回 duplicate_observation，不增加版本。
当前状态由 `state_projection` 给出：values、支持观察 UUID、完整限定词及来源、冲突和未知项。
`current_qualifiers` 保留为生命周期值的兼容视图，不再混入 epistemic、modality、polarity。
状态按 Qualifier 有效时间（其次为事件有效时间）计算，报道到达时间不能冒充有效时间。
LifecycleSpec 定义允许的状态路径；允许跳过未报道的中间步骤，但不生成中间事实。
较晚收到的旧报道不会使 completed 回退；非法迁移保留原状态并记录争议。
同一有效时间的不同状态不任意选取；不同授权/意图/指令主体的状态也不跨主体覆盖。
否认、可能、预测、否定保留为主张，不能直接转成肯定发生的生命周期状态。

`assertion_status` 为 unverified 或 disputed，表示报道及争议，不表示系统已经核实真值。
未知单位不自动换算，来源可信度裁决尚未实现。旧 CanonicalEvent 若无区分角色、身份属性或
状态投影，应从原观察重建后使用新规则；自动身份绑定发生变化时需复核已有成员。

## 设计结论

- AI 分析输出为 `ValuableIntelligenceV4`。
- 持久化时先为其中每个 Event 分配全局 UUID，并独立存储。
- `ValuableIntelligenceV4.EVENTS` 在存储模型中转换为 Event UUID 列表。
- Event 保存 `intelligence_uuid`，用于追溯所属情报，但引擎不读取或理解 `ValuableIntelligenceV4`。
- 引擎仅接受 Event、EventQuery、CanonicalEvent 等独立领域对象。
- 三层为：纯内存分析层、用例整合层、查询适配层。

完整设计见 `DESIGN.md`；已实现的结构边界、三项修复及兼容性说明见
[`SEMANTIC_DECISIONS.md`](SEMANTIC_DECISIONS.md)。

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

`examples/file_ingestion.py` 仅用于演示和测试数据注入，不是持久化层，也不是引擎的运行时 API。
解析时必须传入 Registry，注入时使用目标引擎的 Registry，不自动加载全部领域。
内存、MongoDB 查询仍由 Repository 实现，JSON 文件不承担存储查询能力。

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
