# IntelligenceIntegrationSystem — Agent Guide

> 本文档供 AI 编码助手阅读。如果你第一次接触本项目，请先阅读本文件、`README.md` 和 `doc/event_v4_integration_handoff.md`。

---

## 1. 项目概述

**IntelligenceIntegrationSystem（IIS）** 是一个开源情报（OSINT）整合系统，核心流程为：

```
抓取新闻/情报 → 提交到情报中心 → AI 分析/评分/清洗 → 归档 → 发布/查询
```

系统从全球主流媒体抓取公开新闻（RSS 或列表页），通过 AI（LLM）进行结构化分析、评分、去重，最终将高价值情报归档到数据库，并通过 Web 提供检索、聚合、态势推演等功能。

当前生产主链为 **Event V4**。不兼容、不双读 V1/V2 数据；历史实现统一位于 `recycled/legacy_intelligence/`。

---

## 2. 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| Web 框架 | Flask（主服务）、FastAPI（部分子服务） |
| WSGI 服务器 | Waitress（Windows 默认）、Gunicorn（Linux 可选）、Flask dev server |
| 数据库 | MongoDB（文档存储）、SQLite（用户认证、AI 客户端状态日志） |
| 向量数据库 | ChromaDB + `sentence-transformers`（默认模型 `BAAI/bge-m3`） |
| 爬虫 | Playwright、requests、beautifulsoup4、crawl4ai、feedparser |
| AI 客户端 | OpenAI 兼容 API、SiliconFlow、Zhipu、Google Gemini、Ollama 等 |
| 数据校验 | Pydantic v2 |
| 任务调度 | APScheduler + 自定义 `AdvancedScheduler` |
| 前端 | 服务端渲染 Jinja2 模板 + 少量原生 JS/CSS（无前后端分离） |
| GUI（工具类） | PyQt5 |

---

## 3. 目录结构与模块划分

### 3.1 根目录核心文件

| 文件 | 职责 |
|------|------|
| `GlobalConfig.py` | 全局路径、代理、超时、默认端口等常量 |
| `IISConfig.py` | IIS YAML 配置 schema、默认值、校验和运行时投影 |
| `IntelligenceHub.py` | 兼容入口，只导出 `HubApplication` 与情报数据模型；不构造基础设施。 |
| `IntelligenceHubStartup.py` | **组合根**。读取配置、初始化 MongoDB/AIClientManager，并按配置安装流程端口、恢复、翻译、向量、维护、聚合和图谱扩展。 |
| `IntelligenceHubLauncher.py` | **WSGI 启动器**。自动选择 Waitress/Gunicorn/Flask dev server，带健康检查与自动重启 |
| `CrawlerServiceEngine.py` | **爬虫服务入口**。插件化任务管理、文件系统监控、热重载、爬虫治理后台 |
| `prompts_event_v4.py` | Event V4 AI 分析 Prompt 注册点：基于 `ServiceComponent.prompt_protocols` 的协议族机制，prompt 版本绑定协议（前处理/后处理/校验链）并声明是否参与生产抽样 |
| `prompts_event_v49.py` | v49 精简候选的独立构建入口：直接组合命名章节与权威词表，不读取或改写旧版 Prompt；与 v48 共用输出协议，暂不参与生产抽样 |

### 3.2 主要子目录

- **`AIClientCenter/`** — AI 客户端管理中心
  - 支持多厂商、多账号、Token 轮询、余额监控、故障切换
  - 关键类：`AIClientManager`, `BaseAIClient`, `StandardOpenAIClient`, `OuterTokenRotatingOpenAIClient`
  - 另支持基于本机命令行 Agent 的客户端（`harness/cli.py`：`CodexCLIClient` / `KimiCLIClient`），分 stateless（模拟单次调用）与 session（沿上下文续接、利用缓存省 Token）两种模式，调研见 `AIClientCenter/doc/AgentCLIResearch.md`
  - 配置工厂引擎位于 `AIClientCenter/factory_engine.py`：标准 Client 使用 YAML 工厂配置，内置短名称直接调用 `providers/`（及 harness）中的 `create_*_preset()` 预置；`providers/` 因而同时包含底层适配器与配置预置。自定义工厂可通过 `module:callable`、相对于 YAML 的 `./file.py:callable` 或宿主显式注册扩展，无需修改引擎。独立入口使用 `AIClientCenter/config/config.yaml`，IIS 使用 `_config/ai_clients.yaml`

- **`CrawlTasks/`** — 具体抓取任务模块
  - 每个文件对应一个媒体源的抓取逻辑（如 `task_crawl_bbc.py`, `task_crawl_nhk_ic.py`）
  - 由 `CrawlerServiceEngine.py` 动态加载，支持热重载

- **`IntelligenceCrawler/`** — 爬虫框架与治理
  - `CrawlerPlayground.py`：可视化“所见即所得”生成爬虫配置
  - `CrawlerCodeGenerator.py` / `CrawlerCodePrototype.py`：代码生成
  - `CrawlerGovernanceBackend.py` / `CrawlerGovernanceCore.py`：爬虫治理与调度
  - `CrawlPipeline.py` / `Discoverer.py` / `Extractor.py` / `Fetcher.py`：流水线组件

- **`ServiceComponent/`** — 业务组件层
  - `prompt_protocols.py`：Prompt 协议族机制（协议 = 前处理/后处理/校验链，目录 = 版本注册与抽样声明）
  - `IntelligenceHubDefines_v4.py`：V4 采集输入和 AI 分析结果模型
  - `event_v4_archive.py`：`ArchivedDataV4`、APPENDIX 构造与归档投影
  - `IntelligenceAnalyzerProxy.py`：AI 分析代理，调用 LLM 并解析结果
  - `EventV4QueryEngine.py` / `IntelligenceStatisticsEngine.py`：MongoDB 查询与统计
  - `IntelligenceVectorDBEngine.py` / `IntelligenceAggregationEngine.py` / `DynamicGraphEngine.py` / `adapters/iis_vector.py`：**兼容壳**，实现已迁至 `VectorIntelligence/`，仅 re-export
  - `text_language.py`：语言判定启发式（`needs_translation`，翻译/向量等扩展共享）
  - `IntelligenceScoringEngine.py`：评分引擎
  - `UserManager.py`：用户认证管理（SQLite）
  - `PostManager.py` / `RSSPublisher.py`：文章发布与 RSS 生成

- **`BatchAnalysis/`** — 批量分析子系统（可整体禁用）
  - `service.py`：把 R 状态未超龄积压提交 Provider Batch API（凌晨空闲、半价），完成后同协议链校验并经 `prepare_analysis_outcome` 公共后段回注归档；与实时主链共享 prompt 目录/消息构造/校验/归档后段，Batch 只换 AI 调用通道
  - `config.py`：`optional_subsystems.batch_analysis` 配置读取（enabled/model/lookback_days/max_items/poll_interval_sec）
  - `web.py`：`/api/control/batch_analysis` 端点；`iis_integration.py`：两阶段装配（HubApplication 前创建服务注入扩展，hub_service 后挂 Web）
  - job/item 状态持久化 `_data/analysis_batch.db`，重启续轮询、回注幂等；启用需 `ai_clients.yaml` 有 MiMo key

- **`VectorIntelligence/`** — 向量情报子系统（低耦合，可整体禁用）
  - `extension.py`：向量索引 Hub 扩展（订阅 `archive.completed` / `extension.translation.completed`，自有队列与后台线程，翻译等待基于 `text_language.needs_translation` 自判）
  - `vector_engine.py` / `aggregation.py` / `graph.py`：向量检索、情报聚合、动态图谱引擎
  - `search.py`：向量搜索业务（召回→Mongo 回填→`vector_score`→分页）；经 `IntelligenceHubWebService.set_vector_search_handler` 注入 Core，旧 `/intelligences/query?search_mode=vector_*` 入口委托同一实现
  - `web.py`：`/vector/` Blueprint（`/vector/search`、`/vector/clusters`、`/vector/graph` 及 `/vector/api/*`），模板/静态资源在包内
  - `iis_integration.py`：唯一装配入口（`create_vector_intelligence` + `mount_to_iis`），由组合根在 try/except 内调用
  - `config.py`：集合名/chunk 参数单一事实源与 `intelligence_hub.vectordb` 配置读取
  - 详细设计与迁移计划见 `doc/vector_intelligence_subsystem_refactor.md` 与 `doc/20261015_vector_subsystem_coupling_audit_and_plan.md`

- **`EntityAlias/`** — 实体别名子系统（标准名 ⇄ 别名映射，相对独立，可作独立项目）
  - `service.py`：解析（精确→模糊/部分→可选向量）、批量接口、CRUD、查询时别名展开（`expand_names`）
  - `store.py`：JSONL 文本存储（事实源，git 管理，按类型分文件、按标准名排序、原子重写）
  - `index.py` / `normalize.py`：内存精确索引与归一化；`vector.py`：可选最近名向量召回（IIS 集成时复用 VectorDB 独立集合 `entity_alias_names`，模块不 import VectorDB）
  - `model.py`：实体 = 主标准名+`localized`（lang→该语言标准名）+别名（带 `lang`/`role`: abbr/full/former）+ `native_lang` + `type`（person/organization/country/location/animal/commodity/other/uncertain）+ `status`（unverified/verified）+ `description` + `refs`（外部标识，如 wikidata QID）；`resolve` 支持 `lang=<代码>|native` 返回对应语言标准名
  - `wikidata.py`：在线建议源（本地未命中时查 Wikidata；描述性 UA、0.5s 节流、429 退避、SQLite 缓存 7 天、代理可配）；`register_from_wikidata` 预填多语言数据补录回本地
  - `index.py`：精确索引 + bigram 倒排预过滤（≥2 万名字自动启用）；容量实测与拐点见 README
  - `web.py`：`/entity-alias/` Blueprint（resolve/lookup/entities CRUD API）；`iis_integration.py`：装配入口（`optional_subsystems.entity_alias` 配置门控，经 IISConfig 投影为 `entity_alias.*` 运行时键）
  - 设计/容量/改进文档：`doc/20261016_entity_alias_subsystem.md`
  - `data/`：git 管理的别名数据（JSONL）；`skill/SKILL.md`：AI 调用方指南
  - 查询时展开：挂载后组合根把 `expand_names` 注入各子系统 `EventV4QueryEngine.alias_expander`，实体过滤（PEOPLE/LOCATION/ORGANIZATION）自动展开为全别名 `$in` 查询
  - 历史补录：`Scripts/entity_alias_backfill.py scan` 产出未注册实体工作清单 → AI 填写 `action` → `register` 执行

- **`EntityResolution/`** — 与具体情报绑定的实体指称解析（非破坏性旁表）
  - `EntityRef = subsystem + intelligence_uuid + local_entity_id`，以 Event V4 的 `ENT*` 为稳定锚点；不改写权威 `EVENT_EXTRACTION`
  - `service.py`：把单条情报实体引用指定到全局 `ent_*` 或语境 `ctx_*`，支持改派、清除、审计和撤销
  - `store.py`：MongoDB 分开保存全局解析、语境实体、语境解析和审计，避免泛称污染全局固定别名
  - `iis_integration.py`：按子系统装配并注入态势实体图；模块失败时 IIS 主服务和只读图继续运行
  - `Scripts/rebuild_entity_reference_index.py`：从权威归档重建带 `local_entity_id` 的事件索引，默认 dry-run，`--apply` 写入
  - 实体图中未解析 ref 按“名称 + 类型 + 国家”临时连接（不持久化）；人工指定的 `ent_*`/`ctx_*` 优先，可把同名误合并 ref 拆到不同标准实体
  - 登录用户可把图的网络规模设为 1，逐个选择节点、搜索/新建并指定目标；多个引用指向同一 ID 后图节点自然汇聚
  - 设计与实施说明：`doc/20261021_entity_identity_resolution_and_wiki_design.md`、`doc/20261021_entity_resolution_implementation_plan.md`

- **`VectorDB/`** — 向量数据库服务
  - `VectorDBBService.py`：独立 Flask 服务，提供向量存储、检索、聚类 API
  - `VectorStorageEngine.py`：底层存储引擎（基于 ChromaDB）
  - `ClusterAnalysisPipeline.py` / `aggregation/`：聚类分析与离线/在线聚合

- **`Scraper/`** — 抓取抽象层
  - `ScraperBase.py`, `RequestsScraper.py`, `PlaywrightRawScraper.py`, `PlaywrightRenderedScraper.py`, `Crawl4AI.py`

- **`Tools/`** — 通用工具
  - `MongoDBAccess.py`：MongoDB 封装（带导出功能）
  - `DateTimeUtility.py`, `CommonPost.py`, `RSSFetcher.py`, `CyberSecurity.py` 等

- **`MyPythonUtility/`** — 可复用基础设施
  - `AdvancedScheduler.py`, `ArbitraryRPC.py`, `DictTools.py`, `FileSqliteHyridDB.py`, `ObserverNotifier.py`, `ChatLLM.py`, `WsReverseRPC.py` 等

- **`PyLoggingBackend/`** — 自定义日志后端
  - 支持日志文件监控、Web 查看器、历史归档、TLS 线程隔离日志

- **`Workflow/`** — 抓取流程编排
  - `CommonFlowUtility.py`：通用提交流程（提交到 `/collect`）
  - `RssFeedsBasedCrawlFlow.py`：基于 RSS 的简易抓取流程
  - `IntelligenceCrawlFlow.py`：适配 IIS 的抓取流程

- **`Test/`** — 演示/测试脚本
  - 以 `Test` 开头，但多为手动运行的演示代码，非自动化单元测试

- **`Scripts/`** — 运维脚本
  - `UserManagerConsole.py`：用户增删改查
  - `rebuild_vector_index.py`：重建向量索引
  - `MongoDBShiftDatetime.py`, `mongodb_exporter.py`：数据迁移/导出

### 3.3 数据/配置目录（程序与数据分离）

| 目录 | 用途 |
|------|------|
| `_config/` | 用户手动编辑的独立配置：`iis.yaml`、`ai_clients.yaml`、`collector.json` 等 |
| `_data/` | 程序运行时数据（向量库、SQLite、爬虫治理记录）**需重点备份** |
| `_export/` | 程序导出的数据（JSON、MongoDB 导出） |
| `_log/` | 日志文件及历史归档 |
| `_products/` | 产物目录（预留） |

---

## 4. 构建与运行

### 4.1 环境准备

1. 安装 Python 3.11+
2. 安装并启动 MongoDB（默认 `localhost:27017`）
3. 下载向量模型 `BAAI/bge-m3`（供 VectorDB 使用）
4. 克隆仓库并拉取子模块：
   ```bash
   git submodule update --init --recursive
   ```

### 4.2 安装依赖

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r AIClientCenter/requirements.txt
pip install -r MyPythonUtility/requirements.txt
pip install -r IntelligenceCrawler/requirements.txt
pip install -r PyLoggingBackend/requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

若依赖冲突，可尝试：
```bash
pip install -r requirements_freeze.txt
```

### 4.3 配置

1. **主配置**：复制 `_config/iis_example.yaml` → `_config/iis.yaml`
   - 只填写部署差异；默认值、严格 schema 校验和组件运行时投影位于 `IISConfig.py`
   - 主配置使用简短分区：`server`、`database`、`hub`、`access`、`features`、可选 `web`
   - 公开搜索限制位于 `web.public_search`；未登录用户仅允许按内容主时间搜索
2. **AI 服务配置**：复制 `_config/ai_clients_example.yaml` → `_config/ai_clients.yaml`
   - 每项公共字段为 `factory`、`name`、`group`、`enable`（默认 `true`，设为 `false` 可保留但禁用条目），其余参数直接传入工厂；组并发统一配置在顶层 `group_limits`。本机 harness 可使用 `AIClientCenter/config/config.yaml` 独立验证。
3. **用户数据库**：运行 `python Scripts/UserManagerConsole.py` 创建管理员账号

### 4.4 启动服务

完整功能需要启动 **3 个进程**（可分布在不同机器）：

**A. 核心 Web 服务**
```bash
python IntelligenceHubLauncher.py
```
- 默认监听 `0.0.0.0:5000`
- 公开页面：`http://localhost:5000/`
- 后台登录：`http://localhost:5000/login`

**B. 爬虫服务**
```bash
python CrawlerServiceEngine.py
```
- 自动加载 `CrawlTasks/` 目录下的抓取模块
- 内置爬虫治理 HTTP 后台（默认端口 `18000`，含日志查看器）

**C. 向量数据库服务（可选）**
```bash
python VectorDB/VectorDBBService.py \
  --db-path ./_data/VectorDB \
  --model /path/to/bge-m3 \
  --agg-store-dir ./_data/Aggressive
```
- 默认监听 `127.0.0.1:8001`
- 提供语义搜索、相似推荐、情报聚类能力

---

## 5. 代码风格与开发约定

### 5.1 语言与注释
- 代码中注释以 **中文** 为主，部分关键模块（如 `IntelligenceHubDefines_v4.py`）使用英文 docstring
- 日志输出以中文为主，便于运维阅读
- 类名/函数名使用英文，遵循 PEP 8

### 5.2 数据模型
- 所有子系统使用 `ServiceComponent.IntelligenceHubDefines_v4`、`event_v4_archive` 与 `event_engine.extraction`；不得复制事件字段或 Registry 规则
- 跨组件共享的 schema 版本、状态码、集合后缀、内容时间来源和 UUIDv5 命名空间统一定义在 `ServiceComponent.intelligence_contracts`；不得在业务模块重复声明
- `CollectedData`：独立于分析版本的采集端原始数据契约
- `informant`（通常为来源 URL）是来源去重键，不能为空；`UUID` 仅是内部主键，非法或缺失时在 intake 分配一次 `uuid4` 并写入 cache，不得根据 URL 派生 UUID
- `AnalysisResultV4`：V2 顶层分析字段加 `EVENT_EXTRACTION`；以 `TAXONOMY == "NonIntelligence"` 区分低价值结果
- `EVENT_EXTRACTION` 的存储契约始终是完整的 `event-extraction/1.0`；LLM 面向的 Prompt 可使用完整契约（`full`，v41）或核心子集（`core_subset`，v42；v43 起 RATE 也从数字改为等级词，由服务端按 `IntelligenceHubDefines_v4.RATE_LEVELS` 映射为分数）。生产版本以 `prompts_event_v4.build_event_v4_prompt_catalog` 的注册为准；桥接由 `event_engine.extraction.complete_core_extraction` 完成，详见 `doc/20261013_event_v4_extraction_subset_design.md`
- `ArchivedDataV4`：分析结果加 `UUID`、`INFORMANT`、事件派生速查字段和 `APPENDIX`；`EVENT_EXTRACTION` 内嵌且是权威事件数据
- Mongo `_id` 不是业务字段，读取后必须清除；`UUID` 始终表示文章 ID
- `RAW_DATA`、评分、主时间、归档时间、翻译修订等系统元数据均位于 `APPENDIX`，键名只允许在 `intelligence_contracts.py` 定义；基本查询和前端输出默认移除 `APPENDIX`
- 派生事件索引直接保存实体/地域名称快照，不使用实体 UUID；事件索引失败不得阻塞文章归档
- **不要**在业务代码中随意构造裸字典，应通过 `check_sanitize_dict()` 校验

### 5.3 并发模型
- `HubRuntime` 使用事件队列和 worker：`intake.received → analysis.requested → archive.completed`。
  运行时不解析 payload；各子系统可使用各自的数据结构。
- AI worker 数由配置 `intelligence_hub.ai_analysis_thread` 控制，建议 ≤ AI 客户端数量。
- 入口在返回 `queued` 前等待 intake/archive 阶段确认；在途数量由
  `intelligence_hub.max_inflight` 限制，避免 AI 变慢时无限积压。
- 未归档 cache 恢复、翻译、向量索引、导出、实体频率、聚合、图谱均为独立插件；关闭或初始化失败不能阻塞主链路。
- 情报年龄分流：`intelligence_hub.max_live_age_hours`（默认 0=不启用）设置后，intake 按 pub_time/collect_time 计算情报年龄，超龄记录的分析事件进 REPLAY 低优先级车道而非 LIVE 主队列，防止冷启动/久停后旧情报堵主链。
- 情报年龄硬上限：`intelligence_hub.max_analysis_age_hours`（默认 0=不启用）设置后，超旧情报直接不分析——intake 拒绝（cache 标 X 留痕 + 审计，采集端记 SKIPPED），回放/重试时批量标 X 跳过。三层语义：新鲜→LIVE 主队列 / 超龄→REPLAY 慢车道 / 超旧→弃用。
- 恢复插件始终安装（重试到期轮询依赖它）；`intelligence_hub.replay_unarchived` 只控制启动时是否自动回放未处理记录——设为 `false` 即手工模式，经 `POST /api/control/replay_unarchived`（或控制面板按钮）按需回放；`POST /api/control/discard_pending` 支持 `scope: pending|retry|all` 批量弃用未完成/重试中记录。
- cache 的处理终态协议仍使用 `APPENDIX.__ARCHIVED__`；该字段属于运行状态，不是 V1/V2 归档 DTO，恢复器不得另造判定字段。
- `/collect` 应答契约：`resp` ∈ `queued`（已接收）/ `rejected`（明确拒绝，必带 `reason`，如 `collected_data_duplicated`、`validation_error:*`、`invalid_token`，重试无意义）/ `error`（可重试的传输或内部错误）；采集端经 `Workflow.CollectorSubmission.normalize_collect_response` 解析，拒绝记 SKIPPED、错误缓存重试。
- intake 拒绝（校验失败、重复）会写入 `{集合前缀}intake_rejected` 审计集合（uuid/informant/source/title/reason/rejected_at），不进 cache 队列，仅用于离线审计；写入失败不影响 intake。
- 影子分析链路（保守灰度）：配置 `intelligence_hub.shadow_analysis.versions` 后，主链分析成功会对同一输入并行执行影子 prompt 版本的分析，结果只写入 `_data/analysis_shadow/` 供离线对比，不进入归档与索引；影子失败绝不影响主链。`sample_rate`（0-1，默认 1.0）控制影子采样率；`client_group`（默认空）把影子调用定向到指定 AI 客户端分组——定向后即使影子版本与主链相同也会执行（同 prompt 不同模型对比），且影子调用会避开主链所用客户端以保证模型不同；未定向时同版本跳过（避免重复劳动）。抽样判定对整次事件做一次，保证多影子版本配对完整。
- 影子对比界面：recorder 写 JSON 的同时维护 SQLite 索引（`index.sqlite`，启动时回填）；有影子数据的情报在详情页显示「影子对比」入口，对比页 `/shadow/compare/<uuid>` 按单变量规则配对（同模型比 prompt / 同 prompt 比模型；双变量标“不可归因”仅供参考），并排展示 RATE/事件结构/抽取质量差异。CLI 版为 `Scripts/compare_shadow_records.py`。归档 APPENDIX 的 `__EXTRACTION_QUALITY__` 为抽取质量统计（未登记角色、角色-类型冲突、surface 接地率），仅供聚合参考，不参与归档判定。

### 5.4 错误与重试
- AI 分析使用 `tenacity` 进行指数退避重试（最多 3 次）
- 错误统一分类法（`ServiceComponent/analysis_errors.py`）：network / rate_limit / provider_fatal（4xx 非安全）/ content_safety / json_syntax / schema_shape（结构校验）/ semantic_rule（Registry 语义规则）；每类有差异化路由（`RETRY_POLICY`）：结构/语义错误**同客户端带反馈纠错一次**（按名定向），网络/限流换客户端，内容安全直接终态
- HTTP 400（敏感词/参数错误）视为**不可重试**，标记为 `ARCHIVED_FLAG_SENSITIVE`——但会**先换一次带 `low_sensitivity` 标签的客户端兜底一次**（`_config/ai_clients.yaml` 条目可配 `tags: [low_sensitivity]`），兜底也拒绝才终态；`strong` 等标签预留给能力分层
- 其他错误标记为 `ARCHIVED_FLAG_ERROR`
- **IIS 侧效果统计**（`ServiceComponent/analysis_stats.py`）：每次调用与每件终态持久化到 `_data/analysis_stats.db`（模型×prompt版本 的成功率/首过率/敏感率/错误分布/延迟），与 AIClientCenter 观测库分工（那边管客户端健康）；离线报告用 `Scripts/analysis_model_report.py`
- Client 标签通道：`ai_clients.yaml` 条目级 `tags` 列表 → 工厂引擎挂载到 `client.tags`；`AIClientManager.wait_for_available_client(required_tags=...)` 按标签过滤；校验失败的 ClientCenter 反馈已带 `error_class` 标签
- Prompt 前缀缓存、V48 生产消息布局、无损校验归一化、Client 能力分层和异步 Batch 的
  调查结论见 `doc/20261019_ai_analysis_cache_batch_client_strategy.md`；V48 已启用，文档中
  标为后续方向的能力不得视为现有运行能力。
- v49 将公共可选角色与完整 Frame 分组展示，完整保留谓词定义、评级词表和输出协议；
  独立构建、不从历史 Prompt 派生，注册为 `sampling=False` 供人工调试/对照。
  规则对照、长度与验证见 `doc/20261020_prompt_v49_compaction.md`。
- v51 以 v48 为基线，只用紧凑谓词目录和 v50 角色纪律替换谓词/角色章节；生产按
  v48/v51 等概率灰度，v50 已退出抽样但保留显式对照。评估与停止条件见
  `doc/20261021_prompt_v51_role_compaction_gray.md`。

### 5.5 插件/爬虫加载与开关
- `CrawlerServiceEngine.py` 的 `TaskManager` 在启动时扫描 `CrawlTasks/` 加载任务；watchdog 文件监控目前在代码中被注释，**新增/修改任务需重启服务生效**（重载管线 `submit_reload` 存在，恢复 observer 即可热重载）
- 任务级开关：`_config/collector.json` 的 `collector.disabled_tasks`（插件名列表，如 `task_crawl_investing`）在加载前拦截并停掉同名运行中任务，重启生效
- 每个插件需暴露 `module_init(ctx)` 和 `start_task(stop_event)` 两个钩子
- `stop_event` 为 `threading.Event`，插件应在每次迭代中检查其状态以实现优雅退出

### 5.6 日志
- 使用 `PyLoggingBackend` 统一日志管理
- 关键日志文件：
  - `_log/iis.log`：主服务日志
  - `_log/crawls.log`：爬虫服务日志
  - `_log/perf.log`（核心 Web / Hub）：结构化 JSON 性能日志
  - VectorDB 服务使用自包含的 `VectorDB/perf_logger.py`，默认写入 `logs/vector_perf.log`，可通过环境变量 `VECTOR_PERF_LOG` 覆盖；记录搜索/向量化耗时、内存、CPU、队列深度等
- 日志会自动轮转、归档到 `_log/history_log/`
- 可通过 `limit_logger_level()` 压制第三方库噪音

---

## 6. 测试策略

- 项目依赖 `pytest` 和 `pytest-asyncio`，但 `Test/` 目录下目前以**手动演示脚本**为主
- 没有 CI/CD 流水线，也没有自动化测试套件
- 如果需要添加测试，建议：
  1. 在 `Test/` 目录下新增 `test_*.py` 文件
  2. 对 `ServiceComponent` 中的引擎类（如 `EventV4QueryEngine`, `IntelligenceScoringEngine`）编写单元测试
  3. 对 Pydantic 模型进行边界值测试

运行测试：
```bash
pytest Test/
```

---

## 7. 安全注意事项

1. **Token 鉴权**
   - `/collect`（采集提交）和 `/api`（RPC）分别使用独立 Token
   - Token 配置在 `_config/iis.yaml` 的 `access.collector_tokens`、`access.rpc_tokens` 中
   - 生产环境建议设置 `deny_on_empty_config: true`，禁止空配置时放行

2. **用户认证**
   - 后台页面使用 Flask Session（`SECRET_KEY` 每次启动随机生成，持久会话 7 天）
   - 密码使用 `bcrypt` 哈希存储在 `_config/Authentication.db`
   - 可通过 `Scripts/UserManagerConsole.py` 管理用户

3. **代理与网络**
   - `GlobalConfig.py` 中定义了 `DEFAULT_PROXY`（SOCKS5 `127.0.0.1:10808`）
   - 采集外网新闻通常需要代理/VPN，配置在独立的 `_config/collector.json`

4. **数据隔离**
   - 程序目录与数据目录分离（`_config/`、`_data/`、`_log/`、`_export/`）
   - 部署容器时只需挂载这些目录，无需映射整个项目根目录

---

## 8. 常见问题与排查

| 现象 | 排查方向 |
|------|----------|
| AI 分析线程一直等待客户端 | 检查 `_config/ai_clients.yaml` 是否配置正确；检查 AI 服务余额/网络 |
| VectorDB 无法初始化 | 确认 `VectorDBBService.py` 已启动；确认模型路径正确；查看 `_log/iis.log` |
| 向量搜索慢/超时/503 | 检查 VectorDB 的 `logs/vector_perf.log`（或 `VECTOR_PERF_LOG` 指定路径）中 `vectordb_async_search` 耗时；确认 `VECTOR_MAX_CONCURRENT_SEARCHES` 与 `VECTOR_JOB_MAX_WORKERS` 是否满足并发需求；查看 `/api/status/memory` 中 `jobs.search` 指标 |
| 爬虫不执行 | 检查 `CrawlerServiceEngine.py` 是否启动；检查 `CrawlTasks/` 目录下模块是否有语法错误 |
| 登录失败 | 确认已通过 `Scripts/UserManagerConsole.py` 创建用户；检查 `_config/Authentication.db` 是否存在 |
| 依赖安装失败 | 先升级 `pip`；若仍失败，尝试 `pip install -r requirements_freeze.txt` |

---

## 9. 关键外部依赖版本

以下版本在 `requirements.txt` 中明确指定，升级前请充分测试：

- `fastapi==0.115.12`
- `Flask==3.1.1`
- `pymongo==4.13.0`
- `pydantic==2.11.5`
- `chromadb==1.2.1`
- `playwright==1.52.0`
- `crawl4ai==0.6.3`
- `faiss-cpu==1.11.0`
- `torch==2.9.0`（在 `requirements_freeze.txt` 中）

---

## 10. 文档索引

| 文档 | 内容 |
|------|------|
| `README.md` | 中文项目介绍、部署教程、已接入媒体列表 |
| `doc/event_v4_integration_handoff.md` | Event V4 架构、存储契约与切换状态 |
| `doc/hub_runtime_refactor.md` | 当前 HubRuntime、领域端口与外围扩展的边界、事件链和测试说明 |
| `doc/20260817_subsystem_ui_plugin_rendering.md` | 子系统 UI 区块、整页和独立页面扩展协议 |
| `doc/20261013_event_v4_extraction_subset_design.md` | Event V4 抽取 Schema 核心子集协议（v42）设计、依据与灰度路径 |
| `doc/20261017_role_topic_conflict_investigation.md` | 议题槽错位与角色名误用调查、topic 槽类型检测、prompt v45 与 news Registry 1.6 修复 |
| `doc/subsystems_overview.md` | 各可选子系统的装配模式、耦合边界、配置键与挂载时序；含 SubSystem/ 收拢重构计划 |
| `doc/feed_20261017_survey.md` | 数据源普查：死源清单、新源列表页实测（Guardian/Euronews/Moscow Times/TOI/Africanews 等）与接入建议 |
| `AIClientCenter/README.md` | AI 客户端中心说明 |
| `IntelligenceCrawler/README.md` | 爬虫框架说明 |
| `VectorDB/README.md` | 向量数据库说明 |
| `PyLoggingBackend/README.md` | 日志后端说明 |
| `MyPythonUtility/README.md` | 通用工具库说明 |

---

> **最后更新**：由 AI 助手根据项目实际内容整理。若项目结构或配置方式发生变化，请同步更新本文件。
