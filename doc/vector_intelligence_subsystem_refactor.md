# IIS 向量情报子系统重构交接

> 状态：设计确认，尚未实施
>
> 目标：拆分 IIS 内与向量数据库有关的适配、索引、搜索、聚合、图谱和页面代码。
>
> 非目标：不修改 `VectorDB/` 项目，不新增必须独立部署的服务，不改变 Event V4
> 归档的权威性。

## 1. 已确认的设计决策

IIS 内的向量能力应重构为一个可选的 `VectorIntelligence` 子系统。该子系统与
`SituationViews` 一样保持代码和生命周期独立，但在正常 IIS 部署中仍运行于同一个
Flask/WSGI 进程：

- 后台能力通过 post-process hook 安装到 HubRuntime；
- 页面和 API 通过独立 Flask Blueprint 挂载到 IIS；
- hook、Blueprint 和导航入口均由组合根根据外部配置装配；
- Hub Core 不导入向量引擎、VectorDB 客户端、聚类、图谱或向量页面代码；
- IIS 主程序不启动、检查或等待 VectorDB；
- VectorDB 不可用只影响向量子系统，不得影响采集、分析、归档和普通 MongoDB 查询；
- MongoDB 中的 Event V4 归档始终是文章事实源；待处理队列只保存任务标识，不复制并
  固化文章内容；
- 默认只处理子系统启用后收到的新归档/翻译事件。历史重建必须显式执行，不在启动时
  自动扫描全部历史数据。

目标结构不是新增一条 `IIS -> MongoDB -> 独立同步服务 -> VectorDB` 链路，而是：

```text
IIS Flask / HubRuntime
  ├─ Core pipeline
  │    └─ archive.completed
  ├─ Translation extension
  │    └─ translation.completed
  ├─ VectorIntelligence hook
  │    └─ 自有队列、worker、重试和 VectorDB 访问
  └─ VectorIntelligence Blueprint
       └─ 搜索、相似情报、聚类、图谱页面及 API
```

## 2. Core 与扩展的边界

### 2.1 Core 保留的职责

Core 仅保留与所有扩展通用的能力：

- `HubRuntime`、事件投递和插件生命周期；
- `archive.completed` 等通用流水线事件；
- `translation.completed` 等由扩展公开的稳定事件契约；
- Event V4 归档和普通 MongoDB 查询；
- Flask app、登录保护 wrapper 和通用文章详情路由；
- 由组合根接收已经构造好的扩展列表。

Core 不应出现以下知识：

- VectorDB 地址、客户端、就绪状态和集合名称；
- 向量文档构造、分块参数、相似度阈值和 embedding 模型；
- 向量队列、失败重试和索引状态；
- 聚类计划和动态图谱任务；
- 向量搜索的限流、页面模板和 API 路由；
- `services["vector"]`、`services["aggregation"]`、
  `services["dynamic_graph"]` 等向量专用 service locator 项。

### 2.2 post-process hook 的含义

翻译和向量都属于归档后的 post-process 能力，但不应把慢速 AI/HTTP/VectorDB 调用直接
放入 Core 的 `postprocess` worker。hook 收到事件后只做快速入队，实际工作由扩展自有
worker 执行：

```text
archive.completed
  ├─ TranslationExtension.enqueue(UUID)
  └─ VectorIntelligence.enqueue(UUID, archive_revision)

translation.completed
  └─ VectorIntelligence.enqueue(UUID, translated_revision)
```

这样既能把扩展编排在 post-process 阶段，又不会让 VectorDB 或翻译服务阻塞数据库归档。

向量 hook 不应持有翻译扩展对象，也不应继续使用
`translation_extension.should_defer_index` 这种直接对象依赖。是否等待翻译，应由向量子系统
依据 MongoDB 文档中的翻译状态和自身配置判断。

## 3. 队列与一致性

### 3.1 日常新增数据

默认处理路径由运行时事件驱动，不需要持续扫描全部 MongoDB：

1. IHub 完成 MongoDB 归档；
2. Core 发布 `archive.completed`，不等待向量处理；
3. 向量 hook 将 `subsystem + UUID + revision` 放入自己的队列；
4. worker 处理时从 MongoDB 重新读取权威文章；
5. summary/fulltext 索引成功后标记任务完成。

队列元素建议只包含：

```text
subsystem
UUID
event_type
source_revision
index_version
```

不得把事件 payload 中的整篇文章当成长期事实源，否则翻译或修订后容易索引旧内容。

### 3.2 第一版与持久化

第一版允许使用子系统内部内存队列，以尽快完成代码边界拆分。若需要保证 IIS 异常退出后
未完成任务可恢复，则在子系统目录内增加 SQLite 持久任务表；这仍是子系统内部实现，不得
回写向量专用状态到 Event V4 权威归档。

建议任务状态包含：

```text
subsystem, UUID, source_revision, index_version,
status, retry_count, last_error, created_at, updated_at
```

内存队列只负责唤醒和调度，重启时重新加载 `pending`/`failed` 任务。

### 3.3 历史数据

子系统首次启用时不遍历旧归档。旧数据仅通过显式管理命令处理，例如：

```text
vector-intelligence rebuild
vector-intelligence backfill --start ... --end ...
vector-intelligence reindex --uuid ...
```

可提供“检查最近 N 小时未索引文章”的人工修复或有限对账工具，但它不是日常正确性链路，
也不能在 IIS 启动时自动执行全历史回填。

## 4. Web 与发布系统集成

`SituationViews` 已提供目标模式：`SituationViews.iis_integration.mount_to_iis()` 接收现有
Flask app、登录 wrapper 和 `SubsystemRegistry`，再由 `SituationViewsService` 注册独立
Blueprint。`SituationViews/standalone.py` 只是开发和独立验证入口，IIS 集成时不会另起
服务。

向量页面应采用同样方式：

```text
VectorIntelligence/
  web.py                 # Blueprint 和路由
  iis_integration.py     # IIS 薄装配层
  templates/
  static/
```

推荐路由命名空间：

```text
/vector/
/vector/search
/vector/clusters
/vector/graph
/vector/api/status
/vector/api/search
/vector/api/similar/<uuid>
/vector/api/clusters/...
/vector/api/graph/...
```

Blueprint 注册过程不得等待或探测 VectorDB。VectorDB 未就绪时：

- IIS 仍正常启动；
- `/vector/` 显示明确的暂不可用状态；
- `/vector/api/*` 返回结构化 `503`；
- 子系统后台自行重试连接；
- 普通文章列表、详情和 MongoDB 搜索不受影响。

文章详情仍由 IIS Core Web 提供。向量 API 返回结果时应给出对应子系统的 `detail_url`，
前端不自行猜测 URL 前缀。

## 5. 搜索拆分与兼容

当前 `IntelligenceHubWebService` 将 MongoDB 搜索和向量搜索混在
`/intelligences/query`，同时承担相似文章文本构造、MongoDB 回填、分页、访客限流和
错误处理；根路由与子系统 Blueprint 中还存在重复实现。这是 Web 层最主要的耦合点。

目标状态：

- `/intelligences/search` 和 `/intelligences/query` 只负责普通 MongoDB 搜索；
- `/vector/search` 和 `/vector/api/search` 由向量 Blueprint 独立负责；
- 向量子系统自行完成 VectorDB 召回、MongoDB 批量回填、过滤、排序和公开 DTO 投影；
- `vector_score` 仍作为查询时临时字段返回，不写回权威归档；
- 访客向量搜索限流和并发限制迁移到向量 Blueprint；
- 详情页“相似情报”按钮改为
  `/vector/search?mode=similar&subsystem=<name>&reference=<uuid>`。

为避免已有书签和前端调用失效，迁移期保留旧入口兼容：

```text
/intelligences?search_mode=vector_similar&reference=...
/intelligences/query?search_mode=vector_text
/intelligences/query?search_mode=vector_similar
```

旧页面深链应保留参数并跳转到 `/vector/search`。旧 API 可暂时通过薄兼容适配器转交新
搜索实现；新页面必须直接调用 `/vector/api/search`。确认没有旧调用后再删除兼容层。

向量搜索返回结构在兼容期维持现有列表渲染器需要的形状：

```json
{
  "results": [{"UUID": "...", "vector_score": 0.82, "detail_url": "..."}],
  "total": 1,
  "page": 1,
  "per_page": 10
}
```

## 6. 建议代码归属

建议建立以下目录，名称可在实施前统一：

```text
VectorIntelligence/
  __init__.py
  extension.py            # Hub hook、生命周期和快速入队
  worker.py               # 队列、重试、VectorDB 就绪处理
  task_store.py           # 可选 SQLite 持久队列
  vector_engine.py        # 现 IntelligenceVectorDBEngine
  aggregation.py          # 现 IntelligenceAggregationEngine
  graph.py                # 现 DynamicGraphEngine
  web.py
  iis_integration.py
  templates/
  static/
```

以下现有实现属于迁移对象：

- `ServiceComponent/adapters/iis_vector.py`；
- `ServiceComponent/IntelligenceVectorDBEngine.py`；
- `ServiceComponent/IntelligenceAggregationEngine.py`；
- `ServiceComponent/DynamicGraphEngine.py`；
- `Scripts/rebuild_vector_index.py` 中的 IIS 向量投影和配置逻辑；
- `templates/intelligence_cluster_list.html`、`templates/intelligence_graph.html`；
- 对应的 cluster/graph JS 和向量页面资源；
- `IntelligenceHubWebService.py` 中的向量搜索、聚类和动态图谱路由。

翻译扩展也应采用相同的外部 hook/编排边界，但不要求在本次向量重构中同时重写全部翻译
实现。当前重构必须至少消除向量扩展对翻译扩展实例的直接引用。

## 7. 外部配置与组合根

配置表达“安装哪些 post-process 扩展”，而不是让 Core 内置分支：

```json
{
  "intelligence_hub": {
    "postprocess": {
      "extensions": [
        {"name": "translation", "enabled": true},
        {
          "name": "vector_intelligence",
          "enabled": true,
          "config_file": "_config/vector_intelligence.json"
        }
      ]
    }
  }
}
```

具体工厂由 Core 之外的扩展 catalog/composer 注册。不要直接从不可信配置任意 import Python
路径。组合根只接收 composer 构造出的 `extensions` 和 Web contributions，再分别安装到
HubRuntime 和 Flask app。

向量子系统配置负责：

- VectorDB URL；
- summary/fulltext 集合及分块参数；
- worker 数、队列容量和重试策略；
- 翻译等待策略；
- 搜索阈值、公开限流和并发限制；
- 聚类及图谱开关；
- Blueprint URL 前缀。

## 8. 分阶段实施

### 阶段 A：建立边界，不改行为

1. 新建 `VectorIntelligence` 包和外部 composer。
2. 将 `IISVectorExtension` 迁移为包内 hook/worker。
3. VectorDB 客户端初始化、就绪等待和重试移入子系统后台。
4. Startup 不再检查、启动或等待 VectorDB。
5. 保持现有事件、索引集合和搜索行为，通过测试确认没有功能变化。

### 阶段 B：拆 Web Blueprint

1. 迁移向量搜索、相似情报、聚类、动态图谱路由。
2. 迁移模板和静态资源。
3. 使用 `iis_integration.mount_to_iis()` 风格挂载。
4. 将主站导航和详情页相似情报按钮指向新命名空间。
5. 保留旧 URL/API 兼容层。

### 阶段 C：清理 Core

1. 从 `HubApplication` 删除 `vector_search` 构造参数和
   `vector_search_intelligence()`。
2. 从 `IntelligenceHubStartup` 删除向量专用构造、service locator 和聚合调度。
3. 从 `IntelligenceHubWebService` 删除向量、聚类和动态图谱实现。
4. 删除 Core 对 `VectorDB` 的 import 和进程监控。
5. 完成依赖审计后，从 IIS Core 依赖中移除只供向量能力使用的重型包。

### 阶段 D：可靠性增强

1. 按需要增加子系统 SQLite 持久任务队列。
2. 提供显式历史 backfill/rebuild 命令。
3. 增加最近时间窗的人工对账和失败任务管理页面。
4. 增加索引 schema/model 版本迁移能力，避免不同 embedding 版本混用。

## 9. 验收条件

- 关闭 VectorDB 时，IIS 可以正常启动、采集、分析、归档和普通搜索；
- IIS 启动时间不受 VectorDB 连接超时影响；
- VectorDB 恢复后，子系统能够继续消费自己尚未完成的任务；
- 新归档和翻译完成事件不会阻塞 Core postprocess worker；
- 默认不自动向量化启用前的历史归档；
- 历史索引只通过显式 rebuild/backfill 触发；
- 向量子系统目录可整体禁用，Core 不发生 import 或路由错误；
- 向量页面使用独立 Blueprint，但复用 IIS 登录会话、端口和文章详情页面；
- 新旧搜索在兼容期返回相同的公开文章字段、`vector_score` 和分页结构；
- `HubApplication`、Core pipeline 和普通 Web 查询不导入 VectorDB 或向量业务模块；
- 聚类和动态图谱故障不影响主归档链。

## 10. 实施时优先检查的现有耦合点

- `IntelligenceHubStartup.py`：VectorDB 客户端、向量扩展、聚合、图谱、调度和 service
  locator 的集中装配；
- `ServiceComponent/HubApplication.py`：`vector_search` 注入及公开门面；
- `ServiceComponent/adapters/iis_vector.py`：事件订阅、自有线程和内存队列；
- `IntelligenceHubWebService.py`：Mongo/向量混合搜索及重复实现；
- `static/js/article_detail_renderer.js`：相似情报旧深链；
- `static/js/intelligence_search.js`：向量面板、公开限制和统一查询端点；
- `static/js/intelligence_cluster_list.js`：主站聚类 API；
- `templates/intelligence_graph.html`：主站动态图谱 API；
- `systemd/iis-web.service`：IIS Web 对 VectorDB 的启动依赖；
- 主进程的 VectorDB companion process 监控逻辑。

实施的首要原则是先形成 hook 和 Blueprint 的装配边界，再移动业务实现；不要先大规模移动
文件后再补接口。每一步都应保持 IIS 在完全禁用向量子系统时可独立运行。
