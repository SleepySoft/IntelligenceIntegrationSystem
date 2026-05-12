# IntelligenceIntegrationSystem — Agent Guide

> 本文档供 AI 编码助手阅读。如果你第一次接触本项目，请先阅读本文件，再阅读 `README.md` 和 `doc/IntelligenceDesign.md`。

---

## 1. 项目概述

**IntelligenceIntegrationSystem（IIS）** 是一个开源情报（OSINT）整合系统，核心流程为：

```
抓取新闻/情报 → 提交到情报中心 → AI 分析/评分/清洗 → 归档 → 发布/查询
```

系统从全球主流媒体抓取公开新闻（RSS 或列表页），通过 AI（LLM）进行结构化分析、评分、去重，最终将高价值情报归档到数据库，并通过 Web 提供检索、聚合、态势推演等功能。

当前主分支为 **v2**（2026-02-15 起切换），兼容 v1 数据，无需数据库升级。

---

## 2. 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
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
| `IntelligenceHub.py` | **核心引擎**。管理队列、AI 分析线程、后处理线程、向量化线程、定时任务 |
| `IntelligenceHubStartup.py` | **启动组装**。读取配置、初始化 MongoDB/VectorDB/AIClientManager、组装 Hub 和 WebService |
| `IntelligenceHubLauncher.py` | **WSGI 启动器**。自动选择 Waitress/Gunicorn/Flask dev server，带健康检查与自动重启 |
| `CrawlerServiceEngine.py` | **爬虫服务入口**。插件化任务管理、文件系统监控、热重载、爬虫治理后台 |
| `prompts_v2x.py` | AI 分析 Prompt 定义表 |

### 3.2 主要子目录

- **`AIClientCenter/`** — AI 客户端管理中心
  - 支持多厂商、多账号、Token 轮询、余额监控、故障切换
  - 关键类：`AIClientManager`, `BaseAIClient`, `StandardOpenAIClient`, `OuterTokenRotatingOpenAIClient`
  - 配置方式：将 `AIClientConfigExample.py` 复制为 `_config/ai_client_config.py` 并修改

- **`CrawlTasks/`** — 具体抓取任务模块
  - 每个文件对应一个媒体源的抓取逻辑（如 `task_crawl_bbc.py`, `task_crawl_nhk_ic.py`）
  - 由 `CrawlerServiceEngine.py` 动态加载，支持热重载

- **`IntelligenceCrawler/`** — 爬虫框架与治理
  - `CrawlerPlayground.py`：可视化“所见即所得”生成爬虫配置
  - `CrawlerCodeGenerator.py` / `CrawlerCodePrototype.py`：代码生成
  - `CrawlerGovernanceBackend.py` / `CrawlerGovernanceCore.py`：爬虫治理与调度
  - `CrawlPipeline.py` / `Discoverer.py` / `Extractor.py` / `Fetcher.py`：流水线组件

- **`ServiceComponent/`** — 业务组件层
  - `IntelligenceHubDefines_v2.py`：Pydantic 数据模型（`CollectedData`, `ArchivedData`, `ProcessedData` 等）
  - `IntelligenceAnalyzerProxy.py`：AI 分析代理，调用 LLM 并解析结果
  - `IntelligenceQueryEngine.py` / `IntelligenceStatisticsEngine.py`：MongoDB 查询与统计
  - `IntelligenceVectorDBEngine.py`：向量检索封装
  - `IntelligenceAggregationEngine.py` / `DynamicGraphEngine.py`：情报聚合与态势图谱推演
  - `IntelligenceScoringEngine.py`：评分引擎
  - `UserManager.py`：用户认证管理（SQLite）
  - `PostManager.py` / `RSSPublisher.py`：文章发布与 RSS 生成

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
| `_config/` | 用户手动编辑的配置文件 |
| `_data/` | 程序运行时数据（向量库、SQLite、爬虫治理记录）**需重点备份** |
| `_export/` | 程序导出的数据（JSON、MongoDB 导出） |
| `_log/` | 日志文件及历史归档 |
| `_products/` | 产物目录（预留） |

---

## 4. 构建与运行

### 4.1 环境准备

1. 安装 Python 3.10+
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

1. **主配置**：复制 `_config/config_example.json` → `_config/config.json`
   - 修改 MongoDB 地址、Token、向量库路径等
2. **AI 服务配置**：复制 `AIClientCenter/AIClientConfigExample.py` → `_config/ai_client_config.py`
   - 填入实际 API Key、模型地址、优先级、分组限制等
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
- 代码中注释以 **中文** 为主，部分关键模块（如 `IntelligenceHubDefines_v2.py`）使用英文 docstring
- 日志输出以中文为主，便于运维阅读
- 类名/函数名使用英文，遵循 PEP 8

### 5.2 数据模型
- 所有跨模块传输的数据结构必须使用 `ServiceComponent.IntelligenceHubDefines_v2` 中的 Pydantic 模型定义
- `CollectedData`：采集端提交的原始数据
- `ArchivedData`：AI 分析后归档的数据（含 `APPENDIX` 元数据）
- `ProcessedData`：中间处理数据，用于清洗和校验
- **不要**在业务代码中随意构造裸字典，应通过 `check_sanitize_dict()` 校验

### 5.3 并发模型
- `IntelligenceHub` 内部使用 **多线程 + 队列**：
  - `original_queue`：待分析原始数据
  - `processed_queue`：分析完成待归档数据
  - `unarchived_queue`：重启时加载的未归档数据（低优先级）
  - `vectorize_queue`：待向量化的数据
- AI 分析线程数由配置 `intelligence_hub.ai_analysis_thread` 控制，建议 ≤ AI 客户端数量
- 向量化线程在后台无限重试连接 VectorDB，直到成功或收到关闭信号

### 5.4 错误与重试
- AI 分析使用 `tenacity` 进行指数退避重试（最多 3 次）
- HTTP 400（敏感词/参数错误）视为**不可重试**，标记为 `ARCHIVED_FLAG_SENSITIVE`
- 其他错误标记为 `ARCHIVED_FLAG_ERROR`

### 5.5 插件/爬虫热重载
- `CrawlerServiceEngine.py` 中的 `TaskManager` 支持对 `CrawlTasks/` 目录下的 `.py` 文件进行热重载
- 每个插件需暴露 `module_init(ctx)` 和 `start_task(stop_event)` 两个钩子
- `stop_event` 为 `threading.Event`，插件应在每次迭代中检查其状态以实现优雅退出

### 5.6 日志
- 使用 `PyLoggingBackend` 统一日志管理
- 关键日志文件：
  - `_log/iis.log`：主服务日志
  - `_log/crawls.log`：爬虫服务日志
- 日志会自动轮转、归档到 `_log/history_log/`
- 可通过 `limit_logger_level()` 压制第三方库噪音

---

## 6. 测试策略

- 项目依赖 `pytest` 和 `pytest-asyncio`，但 `Test/` 目录下目前以**手动演示脚本**为主
- 没有 CI/CD 流水线，也没有自动化测试套件
- 如果需要添加测试，建议：
  1. 在 `Test/` 目录下新增 `test_*.py` 文件
  2. 对 `ServiceComponent` 中的引擎类（如 `IntelligenceQueryEngine`, `IntelligenceScoringEngine`）编写单元测试
  3. 对 Pydantic 模型进行边界值测试

运行测试：
```bash
pytest Test/
```

---

## 7. 安全注意事项

1. **Token 鉴权**
   - `/collect`（采集提交）、`/api`（RPC）、`/processed`（处理端）分别使用不同的 Token
   - Token 配置在 `_config/config.json` 的 `collector.tokens`、`rpc_api.tokens`、`processor.tokens` 中
   - 生产环境建议设置 `deny_on_empty_config: true`，禁止空配置时放行

2. **用户认证**
   - 后台页面使用 Flask Session（`SECRET_KEY` 每次启动随机生成，持久会话 7 天）
   - 密码使用 `bcrypt` 哈希存储在 `_config/Authentication.db`
   - 可通过 `Scripts/UserManagerConsole.py` 管理用户

3. **代理与网络**
   - `GlobalConfig.py` 中定义了 `DEFAULT_PROXY`（SOCKS5 `127.0.0.1:10808`）
   - 采集外网新闻通常需要代理/VPN，配置在 `config.json` 的 `collector.global_site_proxy`

4. **数据隔离**
   - 程序目录与数据目录分离（`_config/`、`_data/`、`_log/`、`_export/`）
   - 部署容器时只需挂载这些目录，无需映射整个项目根目录

---

## 8. 常见问题与排查

| 现象 | 排查方向 |
|------|----------|
| AI 分析线程一直等待客户端 | 检查 `_config/ai_client_config.py` 是否配置正确；检查 AI 服务余额/网络 |
| VectorDB 无法初始化 | 确认 `VectorDBBService.py` 已启动；确认模型路径正确；查看 `_log/iis.log` |
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
| `doc/IntelligenceDesign.md` | v1 设计理念与情报分类评分标准 |
| `doc/IntelligenceDesign_v2.md` | v2 设计理念与改进点 |
| `doc/iis_v2_concept.md` | v2 概念说明（与 v1 的区别） |
| `doc/IIS_Diagram.drawio` | 系统架构图（可用 draw.io 打开） |
| `AIClientCenter/README.md` | AI 客户端中心说明 |
| `IntelligenceCrawler/README.md` | 爬虫框架说明 |
| `VectorDB/README.md` | 向量数据库说明 |
| `PyLoggingBackend/README.md` | 日志后端说明 |
| `MyPythonUtility/README.md` | 通用工具库说明 |

---

> **最后更新**：由 AI 助手根据项目实际内容整理。若项目结构或配置方式发生变化，请同步更新本文件。
