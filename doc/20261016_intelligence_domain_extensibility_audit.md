# IIS 情报域扩展能力调查与重构建议

> 调查日期：2026-10-16
> 状态：设计记录，尚未完成本文所述的异构情报域改造。

## 1. 术语决定

本文不再用“子系统”同时指代两类不同对象：

| 名称 | 含义 | 例子 |
| --- | --- | --- |
| **情报域（intelligence domain）** | 一类情报的采集输入、Prompt、输出契约、评分、归档和展示规则 | `news`、`finance`、`industry` |
| **可选子系统（optional subsystem）** | 可整体启停、通过编排挂入 IIS 的外围能力 | `translation`、`vector`、`situation`、`intelligence_views` |

现有代码和配置仍使用 `SubsystemRegistry`、`SubsystemContext` 和
`hub.subsystems` 表示情报域。这是历史命名，不代表它们与
`optional_subsystems` 是同一层概念。后续重构的目标命名为：

```yaml
intelligence_domains:   # news / finance / industry
optional_subsystems:    # translation / vector / situation / ...
```

在代码完成迁移之前，不应只修改部署配置中的 `hub.subsystems`，否则当前版本无法加载。

## 2. 调查结论

当前 IIS 可以较顺利地新增一个 **Event V4 同构情报域**，但还不能通过配置新增一个
“自定义 Prompt + 自定义输出 Schema”的异构情报域。

现有 `finance`、`industry` 等情报域主要获得了资源隔离：

- 独立 MongoDB collection 前缀；
- 独立 URL 前缀；
- 可指定 AI Client group；
- 可提供评分参数和 UI 插件。

它们仍共用新闻域的 Event V4 Prompt Catalog、`AnalysisResultV4`、事件 Registry、
评分/归档流程和查询 DTO。因此目前更准确的说法是“同一 Event V4 实现的多个隔离实例”，
而不是多个可独立定义契约的领域插件。

| 能力 | 当前状态 | 说明 |
| --- | --- | --- |
| 独立集合与 URL | 已支持 | `SubsystemRegistry` 按名称创建资源 |
| 独立 AI Client group | 已支持 | `BasePipelinePorts` 从情报域上下文读取 |
| 独立评分参数 | 部分支持 | 仍要求评分器消费 `AnalysisResultV4` |
| 独立生产 Prompt | 未接通 | 每个上下文保存的仍是同一 Prompt 表，生产 pipeline 使用全局 Catalog |
| 独立 Prompt 协议/校验器 | 未支持 | pipeline 只持有一套全局 `PromptCatalog` |
| 任意输出 Schema | 未支持 | 固定校验为 `AnalysisResultV4`，固定归档为 `ArchivedDataV4` |
| 按情报域声明视图能力 | 未支持 | 注册表没有 list/map/entities 能力描述 |
| 多情报域翻译/向量处理 | 未完成 | 现实现主要绑定默认情报域 |

## 3. 主要耦合点

### 3.1 Prompt 目录是 pipeline 级，不是情报域级

`SubsystemRegistry.build_from_config()` 为每个上下文复制同一份
`EVENT_ANALYSIS_PROMPT_TABLE`。真正执行分析的 `EventV4PipelinePorts` 只构造一份
`PromptCatalog`，选择 Prompt 和校验协议时不根据情报域上下文分派。

`prompt_protocols.py` 的抽象本身允许不同 Prompt 绑定不同前处理、后处理和校验器；缺少的
是“情报域 → PromptCatalog”的装配与路由，而不是 PromptProtocol 抽象本身。

### 3.2 Schema 元数据没有进入主链

`_config/subsystems/<name>/schema.json` 当前只加载到 `schema_meta`，不参与输入校验、LLM
结果校验、归档或查询。生产主链固定执行：

```text
CollectedData
  → 全局 Event V4 PromptCatalog
  → AnalysisResultV4
  → EVENT_EXTRACTION / Event Registry 转换
  → V4 评分
  → ArchivedDataV4
  → Event V4 repository
```

与 `AnalysisResultV4` 不兼容的自定义输出会在服务端校验阶段失败，不能仅靠增加
`schema.json` 接入。

### 3.3 视图默认假定 Event V4 投影存在

SituationViews 可以按注册表找到不同情报域的 collection，但地图和实体图都依赖 Event V4
派生事件中的地点、实体、谓词和角色信息。注册表没有表达“此情报域是否支持地图/实体图”，
集成页也不会据此隐藏 Tab。

当前只配置一份固定的态势 profile（情报域、时间窗口和最低评分），不是每个情报域一份
视图能力与范围。没有地域投影的情报域即使能完成归档，也无法自然接入地图。

### 3.4 可选子系统仍存在默认情报域绑定

翻译扩展只处理默认情报域的归档集合；向量索引、聚合和动态图谱的主要装配也绑定默认
情报域。这不影响单一生产域，但不能据此宣称外围能力已经支持任意多个情报域。

## 4. 目标边界

### 4.1 情报域工厂

参照 AI Client 的工厂机制，内置常用 preset，自定义实现使用外部
`module:callable` 或配置相对路径工厂。工厂返回完整的 `IntelligenceDomainDefinition`，
而不是让 Core 根据零散字段猜测如何组装。

建议的最小合同：

```python
@dataclass(frozen=True)
class IntelligenceDomainDefinition:
    name: str
    display_name: str
    input_validator: Callable
    prompt_catalog: PromptCatalog
    result_validator: Callable
    archive_projector: Callable
    repository_factory: Callable
    query_provider_factory: Callable
    scoring_factory: Callable
    view_providers: "DomainViewProviders"
```

内置 `event_v4` factory 封装现有新闻链路。自定义情报域可参考内置 provider 编写自己的
Prompt、Pydantic Schema 和投影代码，不要求修改 IIS Core 或工厂引擎。

### 4.2 公共信封与领域数据

完全放开 MongoDB 顶层结构会迫使列表、搜索、发布时间、权限、翻译和向量功能分别理解
每一种 Schema。建议保留稳定的公共归档信封，并无损保存领域数据：

```text
UUID / INFORMANT / DOMAIN
SCHEMA_NAME / SCHEMA_VERSION
SUMMARY                  # 通用列表和搜索所需的最小投影
DOMAIN_DATA              # 情报域自定义 Schema 的完整结果
APPENDIX                 # 系统时间、评分、来源及运行元数据
```

Event V4 可以继续使用现有存储契约，先由 adapter 暴露等价公共投影；不必为了抽象立即迁移
生产数据。新异构情报域则必须至少提供 `SUMMARY` projector。

### 4.3 视图是可选 provider，不是 Schema 猜测

每个情报域必须提供列表摘要投影；地图和实体关系图是可选 provider：

```python
@dataclass(frozen=True)
class DomainViewProviders:
    list_summary: Callable
    situation_map: Callable | None = None
    entity_graph: Callable | None = None
```

能力由实际 provider 推导，配置只能关闭已有能力，不能把不存在的 provider 强行“配置为
支持”。例如启用地图但工厂没有提供 `situation_map` 时，应在启动校验阶段失败，而不是
运行到页面后显示空图。

集成页面根据当前情报域的有效能力显示 Tab：

- 永远显示该域声明支持的列表视图；
- 没有地域 provider 时隐藏地域视图；
- 没有实体关系 provider 时隐藏实体视图；
- API 端再次校验能力，不能只依赖前端隐藏；
- 时间窗口、评分阈值等固定范围按“情报域 + 视图”配置。

## 5. 目标配置草案

主配置只负责选择情报域和可选子系统，不承载复杂实现参数：

```yaml
intelligence_domains:
  default: news
  enabled:
    news:
      factory: event_v4
      display_name: 国际新闻
      config: ./domains/news.yaml

    finance:
      factory: ./domains/finance.py:create_domain
      display_name: 财经情报
      config: ./domains/finance.yaml

optional_subsystems:
  translation:
    enabled: true
    client_group: translate_small
  vector:
    enabled: false
  situation:
    enabled: true
```

领域文件可控制自身视图范围，但只有工厂提供相应 provider 时才能启用：

```yaml
views:
  list:
    enabled: true
  map:
    enabled: true
    window_days: 7
    min_score: 6
  entities:
    enabled: true
    window_days: 7
    min_score: 6
```

## 6. 推荐实施顺序

1. **术语与能力描述**：引入 `IntelligenceDomainCapabilities`，让注册表和聚合页能够按域
   隐藏不适用的视图；暂不改变 Event V4 数据。
2. **按域 Prompt 路由**：把 `PromptCatalog` 从全局 pipeline 状态移入情报域定义，人工
   调试、影子分析和生产分析都通过同一路由读取。
3. **Event V4 工厂化**：把现有 pipeline、repository、query engine 和投影包装为内置
   `event_v4` factory，保持现网行为。
4. **异构结果合同**：加入领域 `result_validator`、`archive_projector` 和公共摘要投影，
   用一个非 Event V4 的最小示例域验证接入不修改 Core。
5. **外围能力域化**：翻译、向量和态势子系统显式声明适用情报域，并为每个域维护自己的
   队列、存储命名空间和失败隔离。
6. **配置更名**：代码和测试完成后，将 `hub.subsystems` 一次性迁移为
   `intelligence_domains`；项目既然不要求旧配置兼容，就不要长期双读两个名称。

## 7. 验收条件

- 新增一个 Event V4 同构情报域只需配置内置 factory；
- 新增一个异构情报域只需外部 factory、领域配置和 Schema，不修改 IIS Core；
- 两个情报域可同时使用不同 Prompt Catalog 和结果模型；
- 不支持地图的情报域不会出现地图 Tab，也不会注册可调用的地图查询；
- 支持地图的情报域必须提供可验证的地域投影；
- 任一情报域或可选子系统初始化失败，不阻断其他情报域的主链；
- 翻译、向量、态势等可选子系统明确列出自己处理的情报域，不再隐式绑定默认域。

## 8. 本次调查后的文档修正

旧文档中“新增子系统只需增加配置，无需改代码”的说法只适用于 Event V4 同构情报域。
在完成上述工厂、路由和投影合同之前，不应将其解释为支持任意 Prompt 和任意 Schema。
