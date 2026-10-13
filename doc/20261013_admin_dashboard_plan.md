# 管理后台页面收束与运行控制界面规划

**日期：** 2026-10-13（v2，取代当日早些时候的 dashboard 规划）
**范围调整：** 不做新入口页，`posts/index.md` 仍为登录后首页（仅更新链接指向）。
本期目标：① 收束分散的子页面；② 控制台统计信息界面化；③ 控制钩子界面（暂停队列/全局维护模式）。

## 1. 页面收束方案

### 1.1 合并映射表

| 新页面 | 吸收的旧页面 | 说明 |
|---|---|---|
| **`/statistics/overview`**（统计分析） | `/statistics/intelligence_statistics.html`（现有 dashboard 的聚合部分）<br>`/statistics/score_distribution.html`（评分分布）<br>`/statistics/entity_frequency/page`（实体频率，含缓存构建入口） | 三个页面都是 Mongo/SQLite 历史聚合，天然一类；做成单页三区块（或 tab）。现有 dashboard 页中的实时部分（hub/AI/系统）迁出到运行页 |
| **`/monitor/runtime`**（运行监控） | `/monitor/api/dashboard`（系统/进程/线程）<br>现有 dashboard 的实时部分（hub statistics、pipeline stats、AI 概览）<br>控制台周期打印的 `format_hub_status` | 全部实时内存数据，5s 轮询一个聚合端点 |
| **`/control`**（运行控制，新） | 控制钩子界面 + 控制台统计原文块 | 见 §3 |

### 1.2 保留独立的页面（dashboard 化意义不大）

- `/intelligences*`（列表/搜索/详情/聚合列表）、`/intelligence/graph/view`（图谱）——业务页面；
- `/debug/intelligence`（人工调试工作台）——交互重；
- `/monitor/ai-client-dashboard/*`（AI 客户端详情/手动调用 playground）——运行监控页只取 overview 摘要 + 跳转链接；
- `/logger/log_viewer`（流式日志）、`/maintenance/export_mongodb.html`（导出）；
- 爬虫治理（8002）、VectorDB（8001）——独立进程，本期不动，运行监控页只显示"在线/离线"探活。

### 1.3 收束效果

- 统计类页面 3 → 1，监控类页面 2.5 → 1，新增控制页 1；
- `posts/index.md` 链接分组更新指向，页面总数净减 2；
- 被合并的旧路由保留 302 重定向到新页面对应锚点，避免既有书签/文档失效。

## 2. 控制台统计界面化

控制台目前每 2s 轮询、60s 打印一次的 `format_hub_status` 内容（`ServiceComponent/runtime/status.py`），数据源是 `HubApplication.statistics`：

- runtime：emitted/processed/handler_failures、pending_events、queued_by_type/lane、in_flight_submissions；
- 各子系统：archived/dropped/error 计数；
- pipeline runtime stats：attempts/retries/validation_errors/call_ms 等；
- shadow_statistics：recorded/failed。

界面化方式：运行监控页新增一个聚合端点 `/api/runtime/status`（login_required），返回上述 JSON + 一行 `format_hub_status` 文本（原文展示，与控制台口径一致，便于对照日志）。全部为内存计数，无 DB 查询。

## 3. 控制钩子界面

### 3.1 控制项清单

| 控制项 | 作用 | 实现层 |
|---|---|---|
| 暂停采集接收（intake） | `/collect` 返回 503 maintenance，不写入 cache | Web 层开关 |
| 暂停 AI 分析 | analysis 事件在队列中等待，worker 不消费 | pipeline 端口 flag（见 3.2） |
| 暂停影子分析 | shadow 链路不执行 | pipeline flag |
| 暂停翻译/向量回写 | 扩展插件暂停消费 | extension flag |
| **全局维护模式** | 上述全部 + 爬虫提交自然落空；只读页面（列表/搜索/详情/统计）正常可用 | 组合上述开关 |

目标场景：发布页面测试、演示环境展示——**零 AI 调用、零 token 消耗**，页面照常可读。

### 3.2 实现要点

- 新增 `ServiceComponent/runtime/control.py`：`RuntimeControlState`——线程安全的开关集合（`is_paused(scope)` / `pause(scope)` / `resume(scope)` / `status()`），进程内存态，重启即恢复全运行（不持久化，避免"忘了关"）；
- 挂载点：组合根（IntelligenceHubStartup）创建唯一实例，分别注入 WebService（intake 检查）、EventV4PipelinePorts（analyze/shadow 入口检查，暂停时事件留在队列不消费——worker 在取到 analysis 事件后检查 flag，暂停则重新入队或短暂等待，不标 ERROR）、扩展插件（翻译/向量）；
- 队列积压安全：intake 先停则流入停止，积压上限仍由 `max_inflight` 兜底；运行监控页显示积压数；
- 端点：`GET /api/control/status`、`POST /api/control/pause {scope}`、`POST /api/control/resume {scope}`，全部 login_required + 写操作带二次确认（前端 confirm + 后端校验 scope 白名单）；
- 审计：每次开关操作写 `_log/iis.log`（操作人、scope、时间）。

### 3.3 界面

`/control` 页面：上部为各开关的当前状态与切换按钮（含维护模式一键切换），下部为控制台统计原文块与关键计数（同 `/api/runtime/status`）。本期可先只做状态展示 + intake/analysis/全局三个开关，其余开关预留位置。

## 4. 实施顺序

**P1（页面收束）——已完成（2026-10-13）**
1. `/statistics/overview`：合并三统计页（实体频率的缓存构建入口保留在原页，overview 覆盖其查看功能）；
2. `/monitor/runtime`：合并系统监控 + hub 实时状态 + AI 概览；新增 `/api/runtime/status`（含 `format_hub_status` 原文）；
3. 旧统计两页 302 重定向；`posts/index.md` 链接更新；`/monitor/api/dashboard` 保留为 legacy 详情页。

**P2（控制钩子）——已完成（2026-10-13）**
4. `ServiceComponent/runtime/control.py` 的 `RuntimeControlState`（内存态、审计日志、maintenance 总开关）+ intake/analysis/shadow 三开关接入（translation/vector 为预留位）+ `/control` 页面与 `/api/control/*` 端点（login_required）；
5. 顺带修复：WebService 的请求追踪 Timer 改为 daemon 并容许 request_tracer 为空（原实现会阻止进程退出）。

**P3（预留扩展）**
6. 影子/翻译/向量开关接入扩展插件；爬虫治理、VectorDB 探活卡片；
7. 鉴权修补（`/manual_rate`、`/api/sources` 补 login_required）。

## 5. 验收

- 统计 3 页合一、监控 2 页合一，旧链接 302 可达；
- `/control` 可一键进入维护模式：此后无 AI 调用（perf.log 无新分析记录），只读页面正常；
- 恢复后队列自动继续消费，无事件丢失。
