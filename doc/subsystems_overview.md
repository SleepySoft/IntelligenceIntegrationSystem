# 子系统装配总览（2026-10-19）

> 本文是各可选子系统的统一登记处：装配模式、耦合边界、配置键、挂载时机。
> 新增子系统时应同步更新；文末附待办重构计划。

## 1. 统一模式

子系统与 Hub 的关系收敛为三条规则：

1. **配置**：声明在 `_config/iis.yaml` 的 `optional_subsystems.<name>`，经 IISConfig
   严格校验后投影为运行时顶层键 `<name>.*`（与 vector/situation/entity_alias 同层）。
2. **装配**：包内唯一入口 `iis_integration.py`。服务实例在 HubApplication 构造**前**
   创建并注入扩展/services（随 Hub 启停）；Web 端点在 hub_service 就绪**后**挂载
   （Blueprint + login wrapper）。未启用或装配失败不得影响主服务（组合根 try/except）。
3. **耦合**：只依赖窄接口——子系统不知道管线实现叫什么，组合根以 duck-typing
   传入所需端口（registry、outcome_port、hub 门面、wrapper）。

## 2. 子系统清单

| 子系统 | 职责 | 配置键 | 依赖的 Hub 窄接口 | 持久化 |
|---|---|---|---|---|
| **VectorIntelligence/** | 向量索引/搜索/聚合/图谱，`/vector/` 页面与 API | `optional_subsystems.vector` | archive.completed 订阅、VectorDB HTTP | `_data/VectorDB` |
| **EntityAlias/** | 实体标准名⇄别名解析、CRUD、`/entity-alias/` API | `optional_subsystems.entity_alias` | expand_names 注入各 QueryEngine | `EntityAlias/data/`（JSONL，git 管理） |
| **SituationViews/** | 地域/实体态势看板，`/situation/` | `optional_subsystems.situation` | v4_events 集合、geocoding | `_data/SituationViews` |
| **IntelligenceViews/** | 三视图壳（列表/地域/实体 iframe），`/views` | `optional_subsystems.intelligence_views` | 仅组合既有子页面 | 无 |
| **BatchAnalysis/** | 积压经 Provider Batch（凌晨半价）回注 | `optional_subsystems.batch_analysis` | outcome_port（prompt 目录/协议/`prepare_analysis_outcome`） | `_data/analysis_batch.db` |
| **VectorDB/** | 独立向量库服务进程（独立 git 子模块） | 独立配置 | 无（被 VectorIntelligence 调） | `_data/VectorDB` |

Hub 内置插件（**不是**子系统，属主链）：翻译、未归档回放（IISUnarchivedReplayExtension）、
影子分析、失败样本落盘、实体频率、手动调试分析。

## 3. 挂载时序（组合根 IntelligenceHubStartup）

```
读配置 → Mongo/AIClientManager → pipeline_ports
  → 子系统服务创建（extensions/services 注入）   ← BatchAnalysis 等在此阶段
  → HubApplication(extensions, services)
  → hub_service (Web 层)
  → 子系统 Web 挂载（Blueprint + login wrapper）  ← BatchAnalysis.mount_web 在此阶段
  → 启动
```

## 4. 待办重构：子系统目录统一收拢 SubSystem/

- **现状**：各子系统平铺项目根目录；逻辑已统一（本文件 §1），物理散装。
- **时机**：子系统数量继续增长（8+）时再做；收益仅观感，当前不搬。
- **迁移原则**：只移包，不动 URL 与配置键；先加 `SubSystem/x` 别名导入层过渡，
  运行一段时间后再删旧路径。
- **例外**：`VectorDB/` 是独立 git 子模块，不移动。
- 同步记录在 `doc/TODO.txt`（未跟踪的本地笔记）。
