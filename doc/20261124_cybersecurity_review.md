# IIS 网络安全审视记录

> 创建日期：2026-11-24  
> 状态：持续审视；逐项补充证据、结论与处置记录。

## 1. 总体攻击面清单

1. Agent CLI 处理不可信情报内容时的文件、命令、环境变量和网络权限。
2. 爬虫访问外部 URL 引入的 SSRF、重定向、内网探测和资源耗尽。
3. Web 登录、Session、采集 Token、RPC Token 与管理接口暴露。
4. 外部正文或 AI 输出进入页面后的存储型 XSS。
5. Python、浏览器、模型、CLI、Git 子模块等供应链风险。
6. 数据投毒、向量库污染、去重绕过、额度与队列耗尽。
7. MongoDB、VectorDB、调试端口、反向代理及 Linux 服务权限边界。

风险方法采用“资产—不可信输入—信任边界—特权动作—缓解措施—验证证据”的顺序，
不把 Prompt 或模型自律视为安全边界。

## 2. Agent CLI 权限调查

### 2.1 结论

当前 Codex/Kimi CLI 不适合直接处理不可信新闻正文，风险等级为 **Critical**。

直接链路如下：

```text
攻击者控制的网页/RSS 正文
  → IIS 将正文拼入 Prompt
  → Agent 将正文解释成工具指令（间接 Prompt Injection）
  → 读取 IIS 进程环境、用户文件或执行命令
  → Kimi 可写文件，且运行用户可无密码 sudo 到 root
```

这条链路不要求模型或 CLI 存在传统内存安全漏洞。只要模型服从正文中的恶意指令，
现有工具权限就足以跨越数据层和主机控制层。

### 2.2 当前部署事实

| 项目 | Codex CLI | Kimi Code CLI |
|---|---|---|
| 本机版本 | 0.163.0 | 2.6.0 |
| IIS 调用方式 | `codex exec` | `kimi --prompt` |
| IIS 显式权限参数 | `--sandbox read-only` | 无 |
| 非交互权限语义 | 只读沙箱 | `--prompt` 强制 `auto`（Never Ask） |
| 工作目录 | IIS 项目根 | IIS 项目根 |
| 子进程环境 | 完整继承 IIS 环境 | 完整继承 IIS 环境 |
| Linux 用户 | `sleepy` | `sleepy` |
| 独立容器/用户 | 无 | 无 |

systemd 服务当前没有启用 `ProtectSystem`、`ProtectHome`、`PrivateTmp`、
`PrivateDevices`、`NoNewPrivileges`、能力裁剪或路径白名单。`sleepy` 同时属于
`sudo`、`docker`、`lxd`、`libvirt` 等高权限组，并具有 `(ALL) NOPASSWD: ALL`。

`AgentCLIClient._build_env()` 使用 `os.environ.copy()`，因此 Web Session Secret、AI 服务凭据、
代理变量和其他服务环境变量都会进入 Agent 子进程。工作目录默认使用 IIS 进程当前目录。

### 2.3 受控实验结果

实验仅使用临时目录、假环境变量 `PROBE_SECRET=ENV_CANARY_VISIBLE` 和测试文件：

| 能力 | Codex `read-only` | Kimi `--prompt` |
|---|---:|---:|
| 读取工作目录文件 | 成功 | 成功 |
| 读取工作目录外文件 | 成功 | 成功 |
| 读取继承的环境变量 | 成功 | 成功 |
| 写入工作目录 | 被阻止 | 成功 |
| 写入工作目录外 | 被阻止 | 成功 |

Kimi 会话审计记录显示 `permission.set_mode = auto`。官方文档也明确说明 `--prompt`
在无人交互模式下自动批准普通文件操作和 Shell，只有显式 `deny` 规则仍生效。

尚未对生产密钥、Home 内敏感文件、真实 sudo、外网回连或破坏性命令做验证；已有证据已经
足够判定风险，不应再通过触碰真实秘密来证明。

### 2.4 配置与本地数据附加发现

- `~/.kimi-code/config.toml` 含 Provider API Key，权限为 `0664`；建议收紧为 `0600`。
- Kimi credential 和 session wire 文件为 `0600`。
- Codex `auth.json` 为 `0600`，但部分历史 session 文件为 `0644/0664`，内容可能包含 Prompt、
  路径和工具输出，应统一收紧并设置保留策略。
- `systemd/iis.env` 为 `0664`；即使当前不含秘密，也不应把未来不含秘密作为权限设计前提。

### 2.5 可用限制方法

#### A. 最优方案：生产分析改用普通 AI API

当前任务只是“文本输入 → JSON 输出”，并不需要 Agent 工具。普通模型 API 不具备本地主机
工具能力，攻击结果最多影响模型输出，再由现有 schema/语义校验拦截。这是最小权限方案。

#### B. 若保留 Kimi CLI：使用无工具专用 Agent

Kimi 2.x 支持 `--agent-file`。专用 Markdown Agent 可设置：

```yaml
---
name: iis-json-analyzer
description: IIS JSON-only analyzer
tools: []
subagents: []
---

只根据消息中的情报文本生成要求的 JSON。不得访问文件、命令、网络或外部服务。
```

`tools: []` 会从模型可见能力和执行层同时移除全部工具。该方案应配合独立工作目录、环境变量
白名单和 OS 隔离，不能只依赖系统 Prompt。全局 `[tools]` 也能限制工具，但会影响人工使用
Kimi；专用 Agent 更适合 IIS。

已在本机 Kimi 2.6.0 做受控验证：恶意提示要求创建文件时，无工具 Agent 没有产生任何工具
事件，文件未创建，并明确返回无法访问工具、文件、命令或网络。该限制方法在当前版本有效。

#### C. Codex CLI

`read-only` 已阻止测试写入，但不会阻止读取环境变量或工作区外文件。可追加
`--ephemeral`、`--ignore-user-config`、严格配置和专用空工作目录来缩小暴露，但这些仍不是
完整的读取隔离。官方建议对 Agent 工作负载使用独立计算环境、网络白名单、凭据代理，并让
真实长期凭据留在沙箱之外。

#### D. 两种 CLI 都必须采用的硬边界

1. IIS 主进程不再把完整环境传给 CLI；改用最小环境白名单。
2. Agent 使用独立、无 sudo、无 docker/lxd、无生产数据读取权的 OS 用户或容器。
3. 仅挂载一次调用所需的临时输入；结果通过 stdout 返回，工作区默认只读或空目录。
4. 默认禁止网络；必须联网时仅放行模型服务域名，阻止内网、回环和元数据地址。
5. CPU、内存、进程数、输出大小和墙钟时间均设硬限制。
6. 禁止加载项目 AGENTS、Skills、MCP、Hooks 和用户级扩展配置。
7. Agent session/trace 作为敏感日志管理，限制权限、保留期和导出。
8. 不让运行 Agent 的账号拥有 sudo、Docker socket、LXD、SSH key 或数据库管理凭据。

### 2.6 建议处置顺序

1. **立即**：在隔离完成前暂停 Kimi/Codex 处理不可信生产正文，或至少只保留普通 API Client。
2. **P0**：Kimi 改用 `tools: []` 专用 Agent；CLI 环境改为白名单；工作目录移出项目根。
3. **P0**：把 Agent 从 `sleepy` 和 `NOPASSWD sudo` 权限域中分离。
4. **P1**：为 Codex/Kimi 建立容器/systemd 独立服务，落实只读挂载、网络白名单和资源限制。
5. **P1**：增加间接 Prompt Injection 回归测试，验证不能读文件、环境、联网或写入。
6. **P2**：统一收紧配置与 session 文件权限，配置日志保留和密钥轮换。

### 2.7 主要依据

- Kimi `--prompt` 权限语义：<https://moonshotai.github.io/kimi-code/en/reference/kimi-command.html>
- Kimi 工具与审批模型：<https://moonshotai.github.io/kimi-code/en/reference/tools>
- Kimi Agent 文件工具白名单：<https://moonshotai.github.io/kimi-code/en/customization/agents.html>
- Kimi 配置和 deny 规则：<https://moonshotai.github.io/kimi-code/en/configuration/config-files.html>
- OpenAI Agent 沙箱安全原则：<https://developers.openai.com/api/docs/guides/agents-api/environments/security>
- OpenAI 持久 Agent 硬边界案例：<https://developers.openai.com/blog/securing-persistent-agents-like-openclaw-at-openai>

## 3. 后续逐项审视

- [x] Agent CLI 权限与限制能力（本节）
- [ ] 爬虫 SSRF 与资源消耗
- [ ] Web 暴露、认证、Session、Token 和管理接口
- [ ] 外部内容渲染与存储型 XSS
- [ ] 供应链与更新机制
- [ ] 数据投毒、向量污染和额度耗尽
- [ ] 数据库、端口、反向代理和 Linux 服务隔离

