# 公开搜索页（含向量搜索）性能与稳定性方案

> 状态说明（2026-10）：本文保留原始性能方案和人工分析。Hub 已完成事件化重构，
> `IntelligenceHub` 不再持有向量线程；检索经 `HubApplication` 委托给启动组合层安装的
> `IISVectorExtension`，写入由该扩展订阅归档/翻译完成事件处理。下文中的旧 Hub 方法名
> 应按此映射理解，未实现的性能项仍以 VectorDB 服务端为准。

> 目标：在保持 `/intelligences/search` 页面对未登录用户开放、并允许使用向量搜索的前提下，防止资源被滥用，降低 VectorDB 服务被外部查询拖垮的风险，并建立可分析的性能日志。

---

## 1. 现状与风险分析

### 1.1 当前调用链

```
用户浏览器
  → /intelligences/query (IntelligenceHubWebService)
      → HubApplication.vector_search_intelligence()
          → IISVectorExtension.search()
          → IntelligenceVectorDBEngine.query()
              → RemoteCollection.search()  // VectorDBClient
                  → POST /api/collections/<name>/search-jobs  // VectorDBBService
                      → AsyncJobManager ThreadPoolExecutor(max_workers=4)
                          → repo.search()  // VectorCollectionRepo
                              → SentenceTransformer.encode() + ChromaDB.query()
```

- `/intelligences/search` 页面当前需要登录；`/intelligences/query` 接口已取消向量搜索的登录限制。
- 向量搜索使用 `/search-jobs` 异步接口，**客户端仍保持同步阻塞等待**，超时默认 30s。
- VectorDB 服务端使用一个 `ThreadPoolExecutor(max_workers=4)` 处理所有异步任务（搜索、备份、restore、timestamp_stats 等）。
- 写入（向量化和 upsert）由 `VectorStorageEngine._worker_loop` 单线程消费 `queue.Queue(maxsize=100)`，与搜索线程池**不共享线程**，但仍共享：
  - Python GIL（CPU 密集编码）
  - 同一块 GPU/CPU 推理资源
  - ChromaDB/SQLite 文件锁与内存
  - 系统内存（大 `top_n` 会带回大量 chunk）

### 1.2 主要风险

| 风险点 | 说明 | 后果 |
|--------|------|------|
| 外部请求耗尽搜索线程池 | 4 个 worker 被大量长耗时搜索占满 | 新请求排队/超时，向量服务“不可用” |
| 高并发搜索抢占 embedding 推理资源 | `encode()` 是 CPU/GPU 密集型 | 向量扩展的内部索引队列积压 |
| 超大 `top_n` 或深分页 | 当前服务端未限制 `top_n`，web 端仅限制 `page*per_page ≤ 50` | 内存突增、Chroma 查询变慢 |
| 未登录用户无限制 | 可任意访问全文向量库、深分页、宽时间窗 | 资源滥用、被爬虫/脚本刷接口 |
| 缺乏可观测性 | 仅有普通日志，没有按任务/阶段/资源的结构化性能数据 | 出问题后无法定位是超时、内存、CPU 还是崩溃 |

---

## 2. 方案设计

### 2.1 未登录用户限制（Web 层）

目标：允许游客使用搜索，但降低单次与整体成本。

| 限制项 | 登录用户 | 未登录用户 |
|--------|---------|-----------|
| 访问 `/intelligences/search` | 允许 | 允许 |
| Mongo 搜索 `per_page` | ≤100 | ≤20 |
| 向量搜索 `per_page` | ≤50 | ≤10 |
| 向量搜索最大 `top_n` | 50 | 20 |
| 向量搜索最大页码 | 不限 | 2 |
| 允许的全文库 | summary + fulltext | summary  only |
| 允许的向量模式 | vector_text / vector_similar | vector_text only |
| 最小相似度阈值 | 0.0 | ≥0.6（强制拉高） |
| 默认时间窗 | 自定义 | 最近 30 天（强制） |
| 单 IP 速率限制 | 不限 | 10 次/分钟（向量） |
| 全局并发向量搜索 | 不限 | 同时最多 5 个 |

实现位置：`IntelligenceHubWebService.intelligences_query_api` 与 `_do_vector_search`。

### 2.2 VectorDB 服务端保护

目标：即使外部请求很多，也要保证内部 upsert/向量化通道不被饿死，并提供拒绝能力。

1. **分离搜索执行器**：新增独立的 `search_executor`（线程池或 Semaphore），把搜索任务从通用 `job_manager` 中拆出，避免备份/分析任务与搜索互相阻塞。
2. **搜索并发上限**：引入 `VECTOR_MAX_CONCURRENT_SEARCHES`（默认 8）。超过时立即返回 `503 BUSY`，客户端会按现有重试机制退避。
3. **服务端 `top_n` 上限**：`repo.search` 限制 `top_n ≤ 100`，`fetch_k` 相应限制。
4. **内存/队列指标增强**：在 `/api/status/memory` 中增加 `active_searches`、`queued_searches`。
5. **Upsert 工作线程监控**：记录每次 upsert 任务耗时、队列深度、模型编码耗时、任务前后内存。

### 2.3 性能日志体系

目标：关键路径“既在普通日志输出，又单独写入结构化性能日志文件”，便于后续按任务、接口、资源维度分析热点。

设计：

- 核心 Web / Hub 使用 `Tools/PerformanceLogger.py`：
  - 专用 logger `IIS.Performance`，带独立 `RotatingFileHandler`（`_log/perf.log`）与 JSON 格式。
  - 工具函数：`timed()` 装饰器/上下文管理器、`record()` 原始记录、`snapshot()` 采集进程内存/CPU/线程数/队列长度。
- VectorDB 服务使用自包含的 `VectorDB/perf_logger.py`（不依赖 Tools），默认写入 `logs/vector_perf.log`，可通过环境变量 `VECTOR_PERF_LOG` 覆盖。
- 所有记录统一字段：`operation`, `elapsed_ms`, `status`, `client_ip`, `is_public`, `top_n`, `collection`, `result_count`, `mem_rss_mb`, `cpu_percent`, `queue_size`, `error` 等。
- 接入点：
  1. `IntelligenceHubWebService.intelligences_query_api`（整个查询生命周期）。
  2. `HubApplication.vector_search_intelligence` / `IISVectorExtension.search`（向量搜索本身）。
  3. `IISVectorExtension` 的异步索引循环（每次 upsert）。
  4. `VectorDBBService` 的 `/search` 与 `/search-jobs`（排队等待时间、实际搜索时间）。
  5. `VectorStorageEngine._worker_loop` / `_handle_upsert_task`。

### 2.4 评估脚本

新增 `Test/vectordb_stress_test.py`：

- 并发搜索压测（可配置并发数、请求数、top_n、是否带时间过滤）。
- 并发 upsert 压测，模拟内部向量化。
- 输出：P50/P95/P99 延迟、成功率、VectorDB 队列深度、内存 RSS、CPU。
- 用于本地/测试环境复现“大量请求是否影响向量化”。

---

## 3. 实施步骤

1. 实现 `Tools/PerformanceLogger.py` 并在 `IntelligenceHubStartup` 初始化。
2. 修改 `IntelligenceHubWebService`：
   - 开放 `/intelligences/search`；
   - 在 `/intelligences/query` 增加未登录限制、速率限制、全局并发限制；
   - 添加查询性能日志。
3. 修改 `IISVectorExtension`：
   - `search` 增加性能记录；
   - 异步索引循环增加 upsert 性能记录。
4. 修改 `VectorDB/VectorDBBService.py`：
   - 新增搜索并发控制；
   - 在搜索与 upsert 路径记录性能；
   - `main()` 中通过自包含的 `VectorDB/perf_logger.py` 初始化性能日志。
5. 修改 `VectorDB/VectorStorageEngine.py`：
   - 限制 `top_n`；
   - upsert 任务内部记录耗时/内存。
6. 新增 `Test/vectordb_stress_test.py`。
7. 更新 `_config/config_example.json` 与 `doc/AGENTS.md`（如相关配置变化）。

---

## 4. 预期效果

- 未登录用户无法通过大 `top_n`、深分页、全文向量库、宽时间窗消耗资源。
- VectorDB 服务端在搜索洪峰时仍保留拒绝能力（503 BUSY），通用任务线程池不会被搜索占满。
- `_log/perf.log` 提供可解析的 JSON 数据，能直接回答：
  - 哪些 IP/请求耗时最长？
  - 向量搜索排队等待时间 vs 实际执行时间？
  - 向量化任务是否因搜索拥堵而变慢？
  - 内存是否在 upsert/search 过程中异常增长？
