# IIS 情报域注册与路由设计

> 讨论日期：2026-10-26
>
> 状态：已确认的设计方向，部分实施。
>
> 上游调查：[`20261016_intelligence_domain_extensibility_audit.md`](20261016_intelligence_domain_extensibility_audit.md)

## 0. 实施进度

2026-10-26 已完成第一步语义迁移：

- 正式主配置改为 `hub.domains.default/enabled`；旧 `hub.subsystems` 仅作输入兼容。
- 运行时配置投影改为 `intelligence_hub.domains`。
- Web 路由、模板与向量搜索委托使用 `domain_name` / `IIS_DOMAIN`。
- 无领域前缀的 URL 显式绑定 `hub.domains.default`；具名领域继续使用路径前缀。
- 新领域发现端点为 `/api/domains`；`/api/subsystems` 暂作旧采集器兼容。

尚未实施 `CollectedData.domain`、`DomainRegistry/DomainDefinition` 以及按域的
Prompt/Schema/评分/存储注册。因此底层 Runtime 中的 `subsystem` 字段和部分历史类名
目前仍保留。

## 1. 目标与范围

当前 `news` / `industry` / `finance` 只是多个 Event V4 同构实例。MongoDB 集合、
cache、URL 和查询资源已按域隔离，但分析主链仍共用全局 Prompt Catalog、
`AnalysisResultV4`、评分器与归档实现。

本次改造的目标是：

1. 采集数据以 `domain` 作为情报域唯一路由标识。
2. 不同域可注册彼此独立的 Prompt、结果 Schema/校验、评分和存储实现。
3. 保留现有 intake → analysis → archive 流程，不重写 Runtime、重试、
   AI Client 调度、cache 状态和 worker 模型。
4. 当前各域的最终存储结构保持一致，继续复用现有查询引擎和页面。
5. Batch 和 Shadow 保留公共调度机制，但实际能力与内容由各域可选提供。

本次明确不做：

- 不引入通用 `CommonArchiveRecord` 或公共归档信封。
- 不引入可配置的查询 Provider。
- 不改造现有列表、搜索和详情页的数据结构。
- 不预先建立地图、实体图、向量等完整能力声明体系。

## 2. 已验证的现状

以前置调查和当前代码为准：

- MongoDB 集合、URL、cache 和 QueryEngine 已经按现有 `subsystem` 上下文隔离。
- Event Engine 已包含产业和金融谓词，不必仅为产业域另建事件引擎。
- 详情页遍历 `RATE` 字典，没有写死六个维度。
- Shadow 对比会动态合并左右两侧的 RATE 键。
- 列表、搜索、向量和态势层的评分门槛主要消费 `total_score`。
- 相关针对性测试在服务使用的 Python 3.12 环境中曾通过：`68 passed`。

当前阻塞点：

- `EventV4PipelinePorts` 只持有一份全局 `PromptCatalog`。
- `AnalysisResultV4` 与 `map_rate_levels()` 强制新闻域的六维 RATE。
- `IntelligenceScoringEngine` 包含新闻域的维度名、分类乘数与降权规则。
- `prepare_analysis_outcome()` 和 `archive()` 直接写死 Event V4 后处理和存储类型。
- 手工调试虽接收域名，实际返回的仍是全局 Prompt 目录。
- Batch 当前把多个域的积压混入一个使用全局 Prompt 的 job。
- Shadow 的版本选择和校验同样绑定全局 Prompt 目录。

## 3. `domain` 是唯一路由键

### 3.1 采集边界

`CollectedData` 增加 `domain` 字段。采集器提交后，Hub 在 intake 边界立即校验该域
是否已注册；未知域应明确拒绝，不进入 cache。

`domain` 与历史 `subsystem` 不能成为两个可分歧的事实源。实施时若需要
短期迁移，可在入口把旧字段归一为 `domain`；归一后的主链、cache 恢复和重试
只使用 `domain`。是否保留迁移期兼容待实施时最终决定。

### 3.2 分析结果路由

领域不由 LLM 自行输出或选择。分析结果的 `domain` 必须由服务端从已验证的
采集数据继承，避免模型输出导致跨域校验或入库。

由于各域的结果模型可使用 `extra="forbid"`，不应为加入路由信息而修改域内
Schema。建议使用服务端内部信封：

```python
@dataclass(frozen=True)
class DomainAnalysis:
    domain: str
    result: Any
    prompt_version: int
    ai_service: str
    ai_model: str
```

如果最终 MongoDB 文档也需保存域名，由该域的存储实现从信封或上下文
写入，而不信任 LLM 返回值。

## 4. 领域注册合同

### 4.1 最小主链定义

主链只需三类可替换组件：

1. Prompt Catalog：同时包含 Prompt 版本与 `PromptProtocol.validate_result()`，因而
   Prompt 和结果 Schema/校验是同一个注册单元。
2. Scorer：消费该域的规范结果，返回 `0–10` 的 `total_score`。
3. Storage：负责领域后处理、低价值分流、归档投影与实际写入。

```python
@dataclass(frozen=True)
class DomainDefinition:
    name: str
    prompt_catalog: PromptCatalog
    scorer: Callable[[Any, dict | None], float]
    storage: "DomainStorage"
    subsystems: "DomainSubsystems"
```

`PromptCatalog` 已经对校验结果类型无感；各域的 `PromptProtocol` 可直接返回
各自的 Pydantic 模型，Core 不再 import、`isinstance` 或解析 `AnalysisResultV4`。

`DomainStorage` 的具体方法签名在实施时以不改变现有 analysis/archive 两阶段为
准确定，但必须至少覆盖：

- 从领域结果生成归档阶段载荷；
- 低价值结果的判定与写入；
- 高价值结果的 commit；
- 如果该域使用派生索引，负责对应转换与 outbox 语义。

### 4.2 Pipeline 的路由点

现有流程不变，只替换三个全局实现的取值方式：

```python
definition = domain_registry.require(domain_name)

catalog = definition.prompt_catalog
validated = catalog.protocol_for(prompt_version).validate_result(raw_result)
total_score = definition.scorer(validated, domain_config)
prepared = definition.storage.prepare(validated, original_data, metadata, ctx)
```

archive 阶段同样根据载荷中服务端附加的 `domain` 找回同一个
`definition.storage` 执行真正写入。

以下机制保持公共：

- intake 校验和去重；
- cache 状态与恢复；
- LIVE / REPLAY 车道；
- AI Client 调度、标签、限流与切换；
- 错误分类、重试与纠错反馈；
- Runtime 事件和 worker；
- 基础统计。

## 5. 领域目录和启动注册

每个领域的文件集中在独立目录：

```text
IntelligenceDomains/
├── registry.py
├── news/
│   ├── __init__.py          # 只对外暴露 create_domain()
│   ├── prompt.py
│   ├── schema.py
│   ├── scoring.py
│   ├── storage.py
│   └── subsystems/
│       ├── batch.py
│       └── shadow.py
├── industry/
│   └── ...
└── finance/
    └── ...
```

根配置只选择要启用的域及工厂；复杂实现参数可继续放在域自己的配置中：

```yaml
hub:
  domains:
    default: news
    enabled:
      news:
        factory: news
        subsystems:
          batch:
            enabled: true
          shadow:
            enabled: true
            versions: [48]

      industry:
        factory: industry
        subsystems:
          batch:
            enabled: false
          shadow:
            enabled: false
```

程序启动时：

1. 读取启用域配置；
2. 为该域创建已有的 MongoDB/cache/URL 资源上下文；
3. 调用领域工厂 `create_domain(config, resources)`；
4. 把 `DomainDefinition` 注册到 `DomainRegistry`；
5. 校验默认域、URL 前缀和集合命名不冲突；
6. 创建一份公共 Pipeline，运行时按 `domain` 取实现。

## 6. 领域子系统：Batch 和 Shadow

Batch 和 Shadow 不是领域主链的必选顶层字段，而是放在领域子系统配置中：

```python
@dataclass(frozen=True)
class DomainSubsystems:
    batch: "DomainBatchSupport | None" = None
    shadow: "DomainShadowSupport | None" = None
```

边界如下：

- 公共层保留启动、定时、状态轮询、运行控制和统计等机制。
- 领域目录提供 Batch 的 Prompt/消息构造、校验和回注内容。
- Batch 必须按域拆分 job，不得把不同域的记录混入同一个 Prompt job。
- 领域目录提供 Shadow 可用版本、校验和领域对比内容。
- 领域未提供对应子系统，或部署配置未启用时，公共机制直接跳过。
- 配置启用了领域不支持的子系统时，应在启动校验阶段报明确错误。

## 7. 页面和查询路由

当前不实现可替换 QueryProvider。各域最终写入的顶层展示结构保持一致，
继续复用现有 QueryEngine 与前端。

页面必须知道应访问哪个域的集合，该信息由 URL 路径确定，不通过
`?domain=` 查询参数选择数据库。

```text
/                              默认域列表
/intelligences/query           默认域查询
/intelligence/<uuid>           默认域详情

/industry/                     industry 列表
/industry/intelligences/query  industry 查询
/industry/intelligence/<uuid>  industry 详情
```

程序启动时为每个域按 `url_prefix` 注册 Blueprint；Blueprint 闭包已绑定
`domain_name`。服务端根据该名称从注册表取领域上下文和 MongoDB 集合，不接受
浏览器提交的集合名。

模板应获得 `current_domain` 和 `domain_url_prefix`，搜索、分页、详情与返回链接
继承当前域前缀。默认域的根路径由配置确定，是它的简写访问入口。

## 8. 与可选外围子系统的关系

`domain.subsystems.batch/shadow` 是“某个情报域内的分析旁路”。现有
`optional_subsystems.translation/vector/situation/...` 是 IIS 部署级外围能力。
两者暂不合并，避免重新混淆“情报域”和“可选子系统”。

未来如果翻译、向量或态势需要按域替换具体实现，再使用相同模式增加
可选注册点；本次不预先实现。

## 9. 预期验收条件

1. Collector 提交的 `domain` 决定资源上下文和全部分析路由。
2. 未知 `domain` 在 intake 前被明确拒绝。
3. `news` 与 `industry` 可使用同版本号但不同的 Prompt Catalog 与结果 Schema。
4. Core Pipeline 不 import 具体领域结果类型。
5. 各域的评分维度可不同，公共展示仍使用 `0–10` 的 `total_score`。
6. 存储函数由域注册，主链不再直接构造 `ArchivedDataV4`。
7. 各域仍能通过自己的 URL 前缀访问正确集合，不使用 `?domain=`。
8. Batch/Shadow 仅在域提供能力且配置启用时运行；不同域的 Batch 不混合。
9. 新闻域在改造前后的归档、查询和页面行为保持一致。

## 10. 待实施时确定的细节

以下不影响当前方向，但需在编码前明确：

- `subsystem` 到 `domain` 是否提供短期输入兼容，还是一次性切换。
- `DomainStorage` 是一个对象协议，还是 `prepare/store` 函数组。
- 领域工厂使用内置短名、`module:callable`，或同时支持两者。
- `domain` 在最终归档文档中的顶层字段名与 APPENDIX 映射。
- 现有类名 `SubsystemRegistry` / `SubsystemContext` / `EventV4QueryEngine` 的更名时机。
- 未来存储结构分化后的 QueryProvider 和领域页面注册合同；本次不实施。
