# IIS 多子系统设计（SubSystem）

> 2026-07-31 定稿。目标：IIS 支持多个"子系统"（如国际新闻、财经、行业），
> 各子系统使用不同的 prompt 分析、独立的数据集合与访问路径，
> 但分析/处理流程复用同一套 IntelligenceHub 框架。

## 1. 设计决策

| # | 决策 | 说明 |
|---|------|------|
| 1 | 统一 schema | 所有子系统共用 `ArchivedData`；领域额外字段先放 `APPENDIX`（后续再考虑动态/升级 schema） |
| 2 | 单库分集合 | 同一个 MongoDB 库，每子系统一套 collection；每份文档带 `APPENDIX.__SUBSYSTEM__` 标记 |
| 3 | 硬路由、暂不跨系统 | 情报按其 `subsystem` 标记交给对应子系统分析；查询/检索按子系统隔离 |
| 4 | 配置分离 | 入口与公共配置在 `config.json`，子系统详细配置（prompt、集合前缀、URL）独立文件 |
| 5 | Blueprint 前缀 | Web 层用 Flask Blueprint 按子系统注册不同前缀路径 |
| 6 | Hub 拆资源、流程不变 | 子系统资源收敛到 `SubsystemContext`，队列/线程框架不变 |
| 7 | 向前兼容 | 无 `subsystems` 配置时退化为单一默认子系统，行为与重构前一致 |
| 8 | 采集简单兼容 | `CollectedData.subsystem` 透传；子项目（submodule）不动 |

补充决策：

- 所有子系统具名；`default` 指向某个子系统；不具名的默认 URL（根路径）指向默认子系统内容，具名 URL（`/{name}/...`）同时可用。
- 聚合、推荐、向量搜索等重资源功能**当前仅默认子系统**启用（后期可配置）。
- 每个子系统拥有独立的数据访问实例（MongoDBStorage / 查询引擎），互不干扰。

## 2. 核心架构

```
                     config.json
  intelligence_hub.subsystems { default, list[] }
                           |
                  SubsystemRegistry（新增）
       +-------------------+-------------------+
   SubsystemContext      SubsystemContext     SubsystemContext
   name=news             name=finance         name=industry
   collections=intelligence_*  finance_intelligence_*  industry_intelligence_*
   prompts=prompts_v2x  prompt 文件          prompt 文件
   url_prefix=''(根)     /finance             /industry
                           |
      IntelligenceHub（流程不变：队列 + AI 分析线程 + 后处理线程 + 向量化线程）
                           |
      IntelligenceHubWebService（根路由 + 每子系统 Blueprint）
```

### SubsystemContext 资源清单

- `name` / `display_name` / `is_default` / `enabled`
- `url_prefix`：默认子系统 `''`（根路径），其余 `/{name}`
- `collection_prefix`：默认 `intelligence_`（历史名），其余 `{name}_intelligence_`
- 4 个 MongoDBStorage：`{prefix}cached / archived / low_value / recommendation`
- `cache_query_engine` / `archive_query_engine` / `statistics_engine`
- `prompt_table`（version -> prompt）与 `prompt_files`（支持 mtime 热重载）
- `ai_client_group` / `scoring_config`（可选）
- 独立计数器 `stats`（archived / dropped / error）

## 3. 配置

### 3.1 入口（config.json）

```jsonc
"intelligence_hub": {
  "subsystems": {
    "default": "news",
    "list": [
      { "name": "news", "display_name": "国际新闻", "enabled": true },
      { "name": "finance", "display_name": "财经情报", "enabled": true,
        "config_file": "_config/subsystems/finance.json" },
      { "name": "industry", "display_name": "行业情报", "enabled": false,
        "config_file": "_config/subsystems/industry.json" }
    ]
  }
}
```

### 3.2 子系统详细配置（_config/subsystems/finance.json）

```jsonc
{
  "name": "finance",
  "display_name": "财经情报",
  "url_prefix": "/finance",
  "collection_prefix": "finance_intelligence_",
  "prompt_files": ["_config/subsystems/finance/prompt_v1.md"],
  "ai_client_group": null,
  "scoring": null
}
```

- prompt 文件版本号：优先取文件名中的 `_vNN`，否则按列表顺序 1..N；
  修改文件后 5 秒内自动热重载（mtime 检测）。
- 默认子系统未配置 prompt 文件时沿用 `prompts_v2x.py` 的版本表；
  新子系统未配置时临时沿用默认表并打警告。

### 3.3 采集侧

`CollectedData` 新增 `subsystem` 字段（默认空 = 走默认子系统）。
`CrawlContext` 支持 `subsystem` 参数；可通过全局配置 `collector.subsystem`
或任务内 `crawl_context.subsystem = 'finance'` 指定送达目标。
未知子系统名会被 `/collect` 拒绝。

## 4. Web 路由

| 路径 | 说明 |
|------|------|
| `/intelligences`、`/intelligences/search`、`/intelligences/query`、`/intelligence/<uuid>`、`/api/intelligence/<uuid>` | 默认子系统（根路径，历史兼容） |
| `/news/...` | 默认子系统的具名路径（内容与根路径一致） |
| `/finance/...` | finance 子系统的同构页面/接口 |
| `/{name}/prompt?version=` | 查看子系统当前 prompt |
| `/{name}/dry_run` | POST `{"prompt": "...", "data": {...}}`，真实 AI 分析 + 统一 schema 校验，不入队不入库 |
| `/api/subsystems` | 子系统注册表（名称、显示名、前缀、是否默认） |
| `/collect`、`/login`、`/api`（RPC）、统计/导出/图谱等管理页 | 全局路由，保持原路径 |

管理页（Dashboard、实体频率、图谱、导出、聚合）与向量检索当前作用于默认子系统。

## 5. 数据流

```
/collect ---> submit_collected_data
              |  subsystem 字段路由到 SubsystemContext（未知 -> 拒绝）
              |  缓存到 {prefix}cached，标记 subsystem
              +-> original_queue（共享队列）
                    |
        _ai_analysis_worker：按子系统选 prompt / 评分配置 / 校验
              |（结果 APPENDIX 带 __SUBSYSTEM__ 与 AI 领域扩展字段）
              v
        _post_process_worker：按子系统去重 -> 归档 {prefix}archived
              | 默认子系统：翻译/向量化；非默认：跳过（日志提示）
              v
        /{name}/intelligences/query 等按子系统查询
```

## 6. 新增一个子系统（零代码）

1. 在 `_config/subsystems/` 下新增 `xxx.json` 与 `prompt_v1.md`；
2. 在 `config.json` 的 `intelligence_hub.subsystems.list` 增加一行入口；
3. 采集侧把 `subsystem='xxx'` 透传即可。
   数据库集合、URL 前缀、prompt 表自动生成。

## 7. 当前限制（后续可配置）

- 向量化 / 向量搜索 / 聚合 / 推荐 / 图谱 / 实体频率 / 翻译：仅默认子系统。
- 去重按子系统内进行（同一 URL 投两个子系统会重复入库，由采集侧保证标记正确）。
- 导出页与定时导出作用于默认子系统。
- 未接入 web UI 的子系统切换导航（`/api/subsystems` 已预留）。

## 8. 涉及文件

- 新增：`ServiceComponent/SubsystemRegistry.py`、`_config/subsystems/*`
- 修改：`ServiceComponent/IntelligenceHubDefines_v2.py`、`IntelligenceHub.py`、
  `IntelligenceHubStartup.py`、`IntelligenceHubWebService.py`、
  `Workflow/CommonFlowUtility.py`、`Workflow/RssFeedsBasedCrawlFlow.py`、
  `_config/config_example.json`、`templates/*`、`static/js/*`
