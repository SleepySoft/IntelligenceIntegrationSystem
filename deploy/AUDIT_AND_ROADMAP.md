# Linux 单命令部署：可落地性审计与改造路线

审计日期：2026-10-25

## 目标

在一台具备可访问 MongoDB 和至少一个可用 AI Client 的 Ubuntu 22.04/24.04 或 Debian 12
x86_64 主机上，用户通过一个入口完成 IIS 自身的 Python 环境、配置、Crawler 浏览器、
systemd 启动和健康确认；重复执行不得覆盖用户配置或破坏运行数据。

第一阶段不追求“所有功能全开”。默认 `core` profile 只安装 Web、Crawler、Python 依赖和
Playwright Chromium。MongoDB、Codex/Kimi/Ollama 等 AI 程序属于外部前置条件；VectorDB、
桌面 Dashboard、GUI 工具以及开发依赖均为可选能力。

## 首版责任边界

| 对象 | 安装器负责 | 用户/外部系统负责 |
|---|---|---|
| IIS 源码与 Python venv | 创建、更新、校验 | — |
| IIS YAML/JSON 配置 | 生成骨架、校验、保留已有配置 | 填写外部服务信息 |
| systemd | 生成、安装、启动、查看日志 | — |
| Playwright Chromium | 以 IIS 运行用户安装并校验 | — |
| MongoDB | 检查 host/port/认证和 ping | 安装、运行、备份、升级 |
| Codex/Kimi/Ollama/AI API | 检查可执行文件或端点配置 | 安装、登录、密钥、额度、升级 |
| VectorDB | 首版不处理 | 后续可选 profile |

首版服务直接使用发起安装的普通用户运行；`sudo` 只用于安装系统包和 systemd 文件。这样
外部 CLI 的登录状态和用户目录天然保持一致，不创建或迁移额外认证身份。

## 当前不适合直接落地的项目

### P0：会直接阻塞其他机器安装

1. **systemd 与桌面入口绑定当前机器。** `systemd/*.service`、`iis.env` 和 desktop 文件
   写死 `sleepy`、仓库绝对路径、Conda Python、用户 PATH 和向量模型路径。仓库移动、用户
   不同或不用 Conda 时服务无法启动。
2. **运行时路径没有统一外部化。** `GlobalConfig.py` 从源码位置固定推导 `_config`、
   `_data`、`_log` 和 `_export`。Crawler 能读取 `IIS_COLLECTOR_CONFIG`，预检能读取
   `IIS_CONFIG`，但 Web 正式入口仍从 `GlobalConfig.DEFAULT_CONFIG_FILE` 加载，预检与实际
   运行可能读取不同文件。
3. **示例配置不能组成可启动部署。** collector token 为空；主配置默认启用 translation，
   但 AI 示例只有 `codex_cli` 分组而没有 `translate_small`；预检会正确拒绝这套组合。
4. **没有配置初始化器。** Collector/RPC Token、Flask Session Secret、管理员账号和 AI
   配置仍需人工分别创建；`UserManagerConsole.py` 只有交互菜单，不能安全地用于无人值守安装。
5. **依赖没有按运行能力分层。** 根 `requirements.txt` 同时安装 Web、Crawler、VectorDB、
   Torch/ChromaDB、PyQt、Streamlit 和测试工具，导致最小部署体积大、安装慢、失败面扩大。
6. **外部 AI 程序缺少明确检查契约。** Codex/Kimi/Ollama 的安装、登录和升级不属于 IIS，
   但安装器仍需以最终运行用户验证配置引用的命令确实可执行；API Client 只校验配置和必要
   环境变量存在，不发送会消耗额度的测试请求。
7. **MongoDB 仍被 systemd 当作本机受管服务。** 首版已决定 MongoDB 为外部服务，因此新
   单元不再 `Wants=mongod.service`；安装器只按 IIS 配置执行 ping，失败时停止安装并给出
   host/port/认证错误，不负责安装、初始化、备份或升级 MongoDB。
8. **Playwright 安装身份未固定。** Python 包和 Chromium 浏览器是两层安装；浏览器缓存
   属于执行用户。root 安装、普通用户运行会造成“包已安装但浏览器不可用”。

### P1：可以启动，但不适合作为可靠生产部署

1. 缺少 Ubuntu/Debian 干净环境的自动安装测试和明确的 Python 3.11/3.12 兼容矩阵。
2. `requirements_freeze.txt` 是单机全量快照，不是按 profile 可复现的生产锁定文件。
3. 程序与 systemd 同时承担进程重启职责；`IntelligenceHubLauncher.py` 内部监控 WSGI，
   外层 systemd 又配置 `Restart=always` 与 watchdog，需要明确唯一监督边界。
4. `IntelligenceHubStartup.py` 在模块级捕获装配异常后只打印、不重新抛出；外层可能看到
   一个仍存活但路由未完整装配的进程。生产入口必须 fail-fast，并由 systemd 统一重启。
5. 缺少端口冲突、磁盘空间、文件权限、代理连通性、MongoDB 权限和 AI 凭据可用性诊断。
6. 缺少升级、数据库/SQLite/JSONL 备份、失败回滚和版本迁移协议。
7. 服务单元尚未增加适合当前文件写入边界的 systemd hardening。
8. Nginx、TLS、可信代理头和防火墙只存在说明，没有可选择的安装 profile。
9. 安装成功判定还未覆盖 `/maintenance/ping`、Crawler 进程、MongoDB ping 和一次安全的
   AI Client 可用性检查。

### P2：不阻塞首版，可后续增强

1. VectorDB 模型下载、校验、磁盘估算和独立 profile。
2. tmux OperationsDashboard 与图形桌面自启动；无桌面服务器只需后台 tmux session。
3. Docker Compose、多架构镜像和离线安装包。
4. 自动配置反向代理、域名证书、监控和远程备份。

## 服务运行身份

首版不创建专用系统用户，Web、Crawler 和可选 Dashboard 均以发起安装的普通用户运行。
如果入口由 `sudo` 执行，安装器通过 `SUDO_USER` 还原真实用户；拒绝把 `root` 作为长期服务
用户。这样可以直接使用该用户已经配置好的外部 CLI，同时不复制任何认证目录。

API Key 仍只写入权限 `0600` 的 EnvironmentFile，不写进 YAML、命令行参数或安装日志。
专用 `iis` 用户与更严格的服务隔离后置到增强 profile，不阻塞首版。

## 分阶段实施

### M0：审计与诊断基线（本轮）

- 建立独立 `deploy/` 目录。
- 固化本审计、支持矩阵和验收标准。
- 提供无第三方依赖的 `iisctl doctor`，可在安装依赖前运行。
- 为诊断输出建立自动化测试。

验收：当前仓库和干净 Linux 主机都能运行 doctor；只读、不泄露配置值，并用稳定检查码
区分 pass/warn/fail。

状态：**已完成。** 当前实现为 `deploy/iisctl doctor`，自动化测试覆盖系统信息解析、配置
权限和 JSON 输出不泄密。

### M1：运行时路径与配置单一事实源

- `GlobalConfig` 支持 `IIS_CONFIG_DIR`、`IIS_DATA_DIR`、`IIS_LOG_DIR`、
  `IIS_EXPORT_DIR`，默认行为保持不变。
- Web、Crawler、预检和运维脚本统一读取相同的显式配置路径。
- 增加路径投影测试，保证相对路径基于配置或数据根解析。
- 把现有 systemd 文件改为模板，不再提交部署机绝对路径。

验收：将配置和数据放到临时目录后，预检与实际应用读取完全相同的文件；现有仓库内运行
方式不变。

状态：**进行中。** 已完成可写目录环境变量、显式 `IIS_CONFIG` 禁止示例回退、Web 正式
入口/预检/Batch/OperationsDashboard 的路径统一；systemd 模板化和更广的入口验证待完成。

### M2：依赖 profile 与干净环境验证

- 拆分 `core`、`crawler`、`vector`、`gui`、`dev` 依赖。
- 保留顶层完整开发环境入口，避免破坏贡献者工作流。
- 固定首版 Python 3.11/3.12，建立 Ubuntu 24.04、Debian 12 安装验证脚本。
- 以最终服务用户安装并验证 Playwright Chromium。

验收：`core+crawler` 不安装 PyQt、Streamlit、ChromaDB、sentence-transformers 或测试工具；
全新虚拟环境能通过 imports 与 preflight。

### M3：安全、幂等的配置初始化

- 生成相互匹配的 `iis.yaml`、`collector.json` 与 AI Client 配置。
- 使用 `secrets` 生成 Collector/RPC/Session Secret。
- 增加非交互管理员创建命令，密码从 TTY 或权限文件/stdin 读取。
- 以最终运行用户检查配置引用的外部 CLI；API Client 检查所需环境变量存在。
- 已存在配置默认不覆盖；支持 `--force` 时也必须先备份。

验收：初始化结果权限正确、preflight 通过，日志和终端不出现秘密值。

### M4：单命令安装与服务生命周期

- `iisctl install --profile core` 安装 IIS 系统依赖、venv、浏览器和 systemd 单元。
- 提供 `status/start/stop/restart/logs/doctor`。
- 安装过程分阶段记录，失败可重入；卸载默认保留配置和数据。
- 安装前检查外部 MongoDB，启动后检查 Web health 和 Crawler 状态。

验收：满足外部 MongoDB/AI 前置条件的支持系统上，一次安装命令即可进入登录页；第二次
执行不改变 Token、管理员或数据。

### M5：升级、备份与回滚

- 基于 Git tag/Release 安装，不默认跟随活动分支。
- 使用 release 目录和原子 `current` 切换，升级前备份配置、SQLite、JSONL 与 MongoDB。
- 增加 `upgrade/rollback/backup/restore`，校验产物 checksum。

验收：故意制造升级后健康检查失败时，服务自动回到旧 release，用户数据保持完整。

### M6：可选能力

- VectorDB profile、模型下载与校验。
- Dashboard 安装与桌面自启动。
- Nginx/TLS profile。
- Docker Compose 与发布镜像。

## 首版明确不做

- 不支持所有 Linux 发行版；不在未知系统上自动执行包管理命令。
- 不安装、登录、升级或复制任何外部 AI CLI 及其凭据。
- 不安装、初始化、备份或升级 MongoDB，也不开放 MongoDB、VectorDB 或管理端口到公网。
- 不在安装命令参数中接收明文 API Key 或管理员密码。
- 不把 VectorDB 和 GUI 依赖塞进默认 core 安装。
- 不在没有备份与健康确认的情况下实现自动升级。
