# 向量子系统重构：耦合核查、实施计划与 Dry Run 报告（修订版）

> 日期：2026-10-15（v2，按用户反馈修订范围）
> 依据：`doc/vector_intelligence_subsystem_refactor.md`（设计已确认）
> 状态：**已执行（P1/P2/P3/W1/W2/W3a），169 项测试全部通过**
>
> 执行结果摘要：
> - P1：搜索双实现并轨，`IntelligenceHubWebService.py` 删除 349 行闭包副本，根路由委托 `_query_api(None)`
> - P2：`needs_translation` 提取至 `ServiceComponent/text_language.py`（原处 re-export 兼容）
> - P3+W1：新建 `VectorIntelligence/` 包（config/extension/vector_engine/aggregation/graph/search/web/iis_integration），
>   原 `ServiceComponent` 三引擎与 `adapters/iis_vector.py` 变为 re-export 兼容壳；
>   向量扩展不再持有翻译扩展对象引用（`skip_archive_predicate` 移除，改为内置 `needs_translation` 自判）
> - W1：Startup 装配简化为 `create_vector_intelligence()` 调用（聚合 24h 窗口逻辑内收子系统）
> - W2：`/vector/` Blueprint 挂载（search/clusters/graph 页面 + `/vector/api/*`），模板与 JS 迁入包内并修复路径
> - W3a：`search.py` 抽取向量搜索单一实现，Core `_do_vector_search` 注入委托
>
> 有意保留（迁移期兼容，列入 backlog）：旧入口（`/intelligences/query?search_mode=vector_*`、
> `/api/clusters/*`、`/intelligence/graph/*`、旧聚类/图谱页面与模板）仍由 Core 提供且行为不变；
> `HubApplication.vector_search` 门面、service locator 键、systemd 软依赖、SQLite 持久队列、
> rebuild/backfill CLI、`runtime/control.py` 的 `"vector"` scope 接线均未动。
>
> **范围修订（用户确认）**：
> - `VectorDB/` 是独立项目和进程，**完全不动**；
> - `IntelligenceHubStartup.py` 是系统配置和组装层，**保留装配职责**，允许它 import/构造向量组件，不追求 Core 零 import；
> - 重构聚焦三个主要耦合点：**post-process、Blueprint 注册、搜索**。

---

## 第一部分：耦合核查结论（三个主战场）

### 1.1 总体判断

向量模块的重依赖隔离已经很好：chromadb / sentence-transformers 只存在于 `VectorDB/` 服务进程内，IIS 主进程仅通过 `VectorDB.VectorDBClient`（纯 HTTP）通信。真正需要拆的是三处：

| 战场 | 核心问题 | 位置 |
|------|----------|------|
| **Post-process** | 向量扩展被注入翻译扩展对象引用；集合/chunk 硬编码；调度逻辑散在组合根 | `adapters/iis_vector.py`、`IntelligenceHubStartup.py:295-414` |
| **Blueprint 注册** | 聚类/图谱/向量搜索页面和 API 全部混在主 Web 服务根路由，无独立 Blueprint | `IntelligenceHubWebService.py`、`templates/`、`static/js/` |
| **搜索** | Mongo/向量混合搜索 + **双份实现**（实例方法 vs 根路由闭包），限流/缓存等私有状态挂在 Web 类上 | `IntelligenceHubWebService.py:275-576` vs `:1025-1377` |

### 1.2 Post-process 耦合明细

| # | 位置 | 耦合 | 说明 |
|---|------|------|------|
| P-1 | `IntelligenceHubStartup.py:314-315` | **跨扩展对象引用** | `skip_archive_predicate=translation_extension.should_defer_index` 直接注入向量扩展——设计文档点名消除 |
| P-2 | `adapters/iis_vector.py:12` | import `TRANSLATION_COMPLETED` | 事件字符串契约，可保留 |
| P-3 | `iis_vector.py:139-142` | 集合名/chunk 硬编码 | `intelligence_summary(256/30)`、`intelligence_full_text(512/50)`；`vectordb.stores` 配置是死代码；`IntelligenceAggregationEngine.py:71`、`Scripts/rebuild_vector_index.py:34-38` 共三处重复声明 |
| P-4 | `iis_vector.py:170-171` | 单条索引失败仅记日志 | 不重试、无死信 |
| P-5 | `IntelligenceHubStartup.py:386-399, :414` | 聚合调度逻辑在组合根 | 24h 窗口判定 + `add_hourly_task`，应内收进子系统 |
| P-6 | `IntelligenceHubStartup.py:331-346` | 聚合/图谱就绪门控 | `DeferredServicePlugin(lambda: vector_extension.ready, ...)`；图谱取 `vector_extension.summary_engine` |
| P-7 | `AsyncTranslationPatch.py:160-168` | 翻译判定启发式私有 | **坑：原生中文文档无任何标记**，仅凭 MongoDB 文档无法区分"原生中文"与"待翻译"，子系统自判必须复用 `needs_translation()` |
| P-8 | 翻译事件契约 | `extension.translation.completed` | payload 为完整归档文档 dict；文档上 `APPENDIX.__TRANSLATED_REVISION__` 非空即已翻译 |

现有有利条件：`IISVectorExtension` 已是标准 `HubPlugin`（register/start/stop、双后台线程、`ready` 属性），不直接访问 MongoDB；`HubApplication.vector_search` 已是 `Callable` 注入门面。

### 1.3 Blueprint 注册耦合明细

| # | 位置 | 耦合 |
|---|------|------|
| B-1 | `IntelligenceHubWebService.py:1002-1004, 1016-1021` | 聚类/图谱页面在根路由渲染 |
| B-2 | `:1381-1503, 1980-2082` | 聚类 API（`/api/clusters/*`）、图谱 API（`/intelligence/graph/*`）、`trigger_aggregation` 全在根路由，经 `hub.get_service("aggregation"/"dynamic_graph")` 取引擎 |
| B-3 | `:171-173, :187-193` | 聚类响应缓存（TTL 15s）、向量限流器（60s/10 次）、并发信号量（5）全部挂在 Web 类实例上 |
| B-4 | `templates/intelligence_cluster_list.html`、`intelligence_graph.html` + `static/js/intelligence_cluster_list.js` | 模板/JS 在主站目录，且 **JS 中硬编码绝对路径**（`/api/clusters/*`、`/intelligence/graph/*`，不带 `IIS_BASE_PATH`） |
| B-5 | `posts/index.md:11,15`、`posts/index_public.md:5` | 导航入口指向 `/intelligences/clusters`、`/intelligence/graph/view` |
| B-6 | `static/js/article_detail_renderer.js:524-526` | Find Similar 深链 `/intelligences?search_mode=vector_similar&reference={uuid}`，受 `IIS_VECTOR_ENABLED` 抑制 |
| B-7 | 登录保护矩阵 | 搜索/聚类公开（public 限制兜底），图谱三件套 + trigger_aggregation 需登录（`login_required` wrapper 注入即可复用） |

参照模式已验证可复制：`SituationViews/iis_integration.py:15` 的 `mount_to_iis(app, *, config, subsystem_registry, wrapper, data_path)` —— 配置门控、Blueprint 内建、`wrapper or identity` 登录保护、双层 try/except 失败隔离。

### 1.4 搜索耦合明细（最大的债）

| # | 位置 | 耦合 |
|---|------|------|
| S-1 | `IntelligenceHubWebService.py:275-576` vs `:1025-1377` | **5 个函数双份实现**：`_get_combined_params` / `_apply_public_search_limits` / `_do_mongo_search` / `_do_vector_search` / `_perform_search_logic`，实例方法（Blueprint 用）与根路由闭包各一份，文件头注释承认"二者保持逻辑一致"靠人工维持 |
| S-2 | `:30` | 顶层 import `IntelligenceVectorDBEngine`，仅用静态方法 `build_search_text`（`:444, :1332`）构造相似搜索文本 |
| S-3 | `:431-488, :1322-1377` | 向量召回→内存分页→`hub.get_intelligence` Mongo 回填→注入 `vector_score`→排序→`exclude_raw_data` DTO 投影，与 Mongo 搜索混在同一 `/intelligences/query` |
| S-4 | `:433-435, :520-527` | 向量搜索绑死默认子系统（非默认显式拒绝） |
| S-5 | `static/js/intelligence_search.js` | 向量面板走统一端点 `POST {base}/intelligences/query` + `search_mode=vector_text`；public 限制前端逻辑（clamp、最低相似度、提示条） |
| S-6 | 返回结构 | `{results[], total}` + 查询时注入 `vector_score`（不写回归档）；列表渲染器 `intelligence_table_renderer.js` 依赖 `UUID/INFORMANT/vector_score/EVENT_TITLE/...` 字段 |

差异点确认：实例方法版仅多"非默认子系统拒绝向量"校验（根路由即默认子系统，恒真），**并轨无行为差异**。

---

## 第二部分：重构计划（聚焦三战场）

### 2.0 目标结构

```text
VectorIntelligence/
  __init__.py              # 仅导出引擎类（同 SituationViews 惯例，装配入口不导出）
  config.py                # 子系统配置：VectorDB URL、stores、worker/重试、翻译等待、搜索限流、聚类/图谱开关、url_prefix
  extension.py             # VectorIntelligenceExtension(HubPlugin)：事件订阅、快速入队、start/stop、ready
  worker.py                # 队列、索引 worker、VectorDB 就绪等待与重试（现 iis_vector 线程逻辑）
  translation_wait.py      # 翻译等待自判（needs_translation 启发式 + APPENDIX.__TRANSLATED_REVISION__）
  vector_engine.py         # 现 IntelligenceVectorDBEngine（含 build_search_text）
  aggregation.py           # 现 IntelligenceAggregationEngine + 内收的小时调度逻辑
  graph.py                 # 现 DynamicGraphEngine（引擎依赖改 duck typing）
  search.py                # 向量搜索业务：召回→Mongo 回填→vector_score 注入→DTO 投影→分页
  web.py                   # /vector/ Blueprint：页面 + API
  compat.py                # 旧 URL/API 兼容适配层（迁移期）
  iis_integration.py       # 唯一装配入口：create_extension() + mount_to_iis()
  templates/               # 迁入 cluster/graph 模板 + 新增 search/status
  static/                  # 迁入 cluster/graph JS
```

依赖方向单向：`VectorIntelligence` → IIS 契约层（`intelligence_contracts`、`IntelligenceHubDefines_v4`、`runtime`、`pipeline/events`、`extensions/events` 常量）；组合根只在装配点 import `iis_integration`。

### 2.1 前置微重构（不改行为）

| 步骤 | 内容 | 验证 |
|------|------|------|
| **P1 搜索并轨** | `register_routers` 闭包副本（`:1025-1377`）全部委托实例方法（`:275-576`），删除约 350 行重复 | curl diff 三个 search_mode 前后响应 |
| **P2 提取翻译启发式** | `needs_translation()` 从 `AsyncTranslationPatch.py:160-168` 移到 `ServiceComponent/text_language.py`（Core 中立工具），原处改 import | `Test/test_iis_translation_extension.py` 通过 |
| **P3 集合配置单一事实源** | 集合名+chunk 定义收进 `VectorIntelligence/config.py` 默认值，兼容旧 `vectordb.stores`；消除三处重复声明 | 索引集合名与现有数据一致 |

### 2.2 W1：Post-process 解耦

1. 建 `VectorIntelligence/` 包；三个引擎文件**整体迁入**（仅改 import 路径），`DynamicGraphEngine` 对向量引擎的类型 import 改 duck typing。
2. `extension.py` 迁入现 `IISVectorExtension` 逻辑，**删除 `skip_archive_predicate` 参数**，改为子系统自判翻译等待：
   - `archive.completed` → hook 快速入队 `{uuid, event_type, source_revision}`（不持 payload 为事实源）；
   - worker 处理时**从 MongoDB 重读权威文档**；若配置 `translation.wait=true` 且文档无 `__TRANSLATED_REVISION__` 且 `needs_translation(doc)` → 延迟重入队（超时可配，默认 30 分钟，超时按原文索引）；
   - 订阅 `extension.translation.completed` 字符串契约 → 直接入队。
3. 就绪等待/重试/集合创建全部留在子系统后台线程（沿用 `_initialize_forever` 模式，VectorDB 不在 → 无限后台重试，不阻塞启动）。
4. 聚合 24h 窗口调度逻辑（P-5）内收进 `aggregation.py`，经 `ScheduledMaintenancePlugin` 挂载；聚合/图谱的 `DeferredServicePlugin` 就绪门控（P-6）在子系统内部完成，对外只暴露 `services` 字典和 `search` callable。
5. `iis_integration.py` 暴露 `create_extension(config, ...) -> VectorIntelligenceRuntime`（含 extension、services、search callable）；**Startup 保留装配**：读配置 → 调 `create_extension` → `extensions.append` + 绑定 `vector_search` 门面 + 挂 `ScheduledMaintenancePlugin`。不再做的是：24h 窗口判定、集合创建细节、引擎逐个构造。
6. `adapters/iis_vector.py` 保留兼容 re-export 壳（指向新类），迁移期测试不动。

**出口标准**：`vectordb.enabled=false`（当前生产状态）行为与现状完全一致；开启时新归档/翻译事件正常索引；翻译扩展对象不再被向量扩展持有。

### 2.3 W2：Blueprint 注册

1. `web.py` 新建 Blueprint（url_prefix 默认 `/vector`，可配）：
   - 页面：`/vector/`（状态）、`/vector/search`、`/vector/clusters`、`/vector/graph`；
   - API：`/vector/api/status`、`/vector/api/search`、`/vector/api/similar/<uuid>`、`/vector/api/clusters/latest`、`/vector/api/clusters/<id>/members`、`/vector/api/graph/build`、`/vector/api/graph/status/<tid>`、`/vector/api/debug/trigger_aggregation`；
   - 引擎、限流器、聚类缓存（B-3）、public_search 限制全部经构造参数注入；VectorDB 未就绪 → 页面显示暂不可用、API 结构化 503；
   - 登录保护矩阵不变：搜索/聚类公开（public 限制），图谱 + trigger_aggregation 用注入的 `login_required` wrapper。
2. `mount_to_iis(app, *, config, wrapper, ...)` 照搬 SituationViews 模式：配置门控 + 双层 try/except；Startup 在 `register_routers` 后调用，返回值即子系统 handle。
3. 模板/静态资源迁入包内；修复 JS 硬编码路径（B-4）：统一经 `window.IIS_VECTOR_BASE` 注入前缀。
4. 导航与深链更新：`posts/index*.md` → `/vector/clusters`、`/vector/graph`；`article_detail_renderer.js` Find Similar → `/vector/search?mode=similar&reference=<uuid>`（B-6）。

### 2.4 W3：搜索拆分与兼容

1. `search.py` 承接 `_do_vector_search` 全部业务（S-3）：召回→Mongo 回填（注入的 `get_intelligence` callable）→`vector_score`→排序→投影→分页；`build_search_text` 随引擎在包内，Web 层不再 import 引擎（S-2 消除）。
2. 访客限流/并发（B-3）随搜索迁入 Blueprint；public 限制逻辑复用 P1 并轨后的单份实现。
3. `compat.py` 兼容层（由子系统注册到 Core app，迁移期保留）：
   - `/intelligences?search_mode=vector_similar&reference=...` → 302 保留参数跳 `/vector/search?mode=similar&...`；
   - `/intelligences/query?search_mode=vector_*` → 薄适配器转调 `search.py`，返回形状逐字段不变（`results/total`+`vector_score`）；
   - `/api/clusters/*`、`/intelligence/graph/*`、两个旧页面 → 委托/跳转新实现。
4. Core Web 收缩：`IntelligenceHubWebService` 的向量分支变为"注入了 `vector_search_handler` 则委托，否则维持现状返回空"；向量限流/聚类缓存等私有状态移出。
5. `vector_score` 仍为查询时临时字段；新 API 每项结果附 `detail_url`（按子系统前缀生成，前端不猜 URL）。

**出口标准**：新旧入口对相同输入返回相同公开字段与分页结构；VectorDB 关闭时主站全部页面正常；主站 JS 不再出现 `/api/clusters`、`/intelligence/graph` 硬编码。

### 2.5 后续项（本次不做，列入 backlog）

- Startup 残留清理、`services["vector"]` 移除（生产无人读）、`HubApplication.vector_search` 门面删除——待 compat 层下线时一并处理；
- SQLite 持久任务队列、rebuild/backfill CLI（吸收 `Scripts/rebuild_vector_index.py`）、对账页面、`index_version` 迁移（设计文档阶段 D）；
- `systemd/iis-web.service` 的 `Wants=iis-vectordb.service` 软依赖移除；
- `runtime/control.py:19` 预留 `"vector"` scope 接线。

### 2.6 翻译扩展边界

不重写翻译实现。只做：① 消除 P-1 对象引用（W1）；② P2 提取共享启发式。事件契约 `extension.translation.completed` 不变。

---

## 第三部分：Dry Run 推演

### P1 搜索并轨

- **改动**：`IntelligenceHubWebService.py` 根路由 L1034-1101 改为委托 `self._query_api(default_subsystem)`，删 L1103-1377 闭包副本及 L1025-1032 重复 `_get_client_ip`。
- **风险**：低。实例版多出的非默认子系统校验在根路由恒真；参数解析两版逐行比对过，语义一致。
- **验证**：并轨前后 curl 取样 diff：`search_mode=mongo / vector_text / vector_similar` × 登录/游客 × 分页。

### W1 Post-process

- **新建**：包骨架 9 文件（3 引擎为搬迁）。**修改**：`IntelligenceHubStartup.py`（装配段简化，-约 80 行 +约 20 行）、`AsyncTranslationPatch.py`（P2 import）、`adapters/iis_vector.py`（改壳）。
- **关键推演——翻译等待行为差异**：旧逻辑"需翻译→跳过 archive 事件，等 translation.completed"；新逻辑"入队→worker 等待/重试→超时按原文索引，translation.completed 到达后再覆盖索引"。差异：翻译长期不完成时旧逻辑永不索引、新逻辑超时兜底。**有意改进**，超时默认 30 分钟可配。
- **验证**：① `enabled=false` 启动无 VectorDB 连接日志；② 开启 + VectorDB 关闭 → 启动不阻塞、后台重试；③ VectorDB 恢复 → 自动就绪、新归档索引成功；④ 需翻译文档 → 等 translation.completed 后索引的是译文。
- **回滚**：装配段为增量改动，git revert 单步可回。

### W2 Blueprint

- **新建**：`web.py`（约 400 行）+ 4 模板 + 2 JS 迁入。**修改**：`posts/index*.md`、`article_detail_renderer.js`、Startup +约 15 行挂载。
- **风险**：JS 路径遗漏 → 迁移后 grep 复核清单（`/api/clusters`、`/intelligence/graph`、`IIS_VECTOR_ENABLED`）。
- **验证**：四页面手工冒烟（VectorDB 开/关各一遍）；未就绪时 `/vector/api/*` 返回结构化 503、主站不受影响。

### W3 搜索拆分

- **新建**：`search.py`（约 150 行）、`compat.py`（约 120 行）。**修改**：`IntelligenceHubWebService.py`（-约 450 行向量实现，+委托钩子）、`intelligence_search.js`（新页面走 `/vector/api/search`）。
- **关键推演——兼容形状**：旧端点输出 `{results[], total}` + `vector_score`，无 page/per_page 回显（前端用请求值渲染分页）——compat 适配器逐字段对齐；新 API 额外附 `detail_url`，不影响旧渲染器。
- **验证**：curl 矩阵（新旧 × 模式 × 登录/游客）响应 diff；`Test/` 下相关用例；禁用测试：重命名 `VectorIntelligence/` → 主站启动与普通搜索正常 → 恢复。

### 全局风险登记

| 风险 | 等级 | 缓解 |
|------|------|------|
| 搜索并轨引入行为漂移 | 中 | P1 独立成步 + curl diff |
| 翻译等待边缘行为变化 | 中 | 超时可配；translation.completed 保证最终一致；决策日志 |
| 前端硬编码路径遗漏 | 中 | B-4/B-6/S-5 清单逐一 grep 复核 |
| 旧书签/外部 API 调用方未知 | 低 | compat 层保留，确认无调用后另行下线 |
| 向量绑死默认子系统 | 低 | 本次保持现状，blueprint 预留 `?subsystem=` 参数位 |
| 生产 vectordb.enabled=false | — | 正面因素：落地期无向量流量，可安全灰度 |

### 工作量粗估

| 阶段 | 规模 |
|------|------|
| P1-P3 | ~0.5 天 |
| W1 | ~1.5 天 |
| W2 | ~1 天 |
| W3 | ~1 天 |

---

## 第四部分：验收条件映射

| 验收条件（设计文档 §9，按新范围筛选） | 保证阶段 |
|----------|----------------|
| 关闭 VectorDB 时 IIS 正常启动/采集/归档/普通搜索 | W1（现状已满足，保持） |
| 启动时间不受 VectorDB 连接超时影响 | W1（就绪等待全在子系统后台线程） |
| VectorDB 恢复后继续消费未完成任务 | W1（内存队列+重试；持久队列列 backlog） |
| 归档/翻译事件不阻塞 Core postprocess worker | W1（hook 只入队） |
| 默认不索引启用前历史归档 | W1（事件驱动，无启动扫描） |
| 向量页面独立 Blueprint，复用登录会话/端口/详情页 | W2 |
| 新旧搜索兼容期返回相同字段/vector_score/分页 | W3（curl diff 验证） |
| 聚类/图谱故障不影响主归档链 | W1（DeferredServicePlugin 隔离 + 事件链外） |
| ~~Core 不 import VectorDB/向量业务模块~~ | **按用户指示移出验收**：Startup 作为组合根允许装配性 import |

---

## 结论

耦合核查确认三个主战场的病灶：post-process 的跨扩展对象引用与硬编码（W1）、页面/API 无独立 Blueprint（W2）、搜索双份实现与混合流程（W3）。前置 P1-P3 消除最大风险点（双实现并轨、翻译启发式提取、集合配置单一事实源）后，三个战场可按 W1 → W2 → W3 顺序独立落地，每步可验证可回滚。Startup 保留装配职责，VectorDB 不动。

等待批准后从 P1 开始实施。
