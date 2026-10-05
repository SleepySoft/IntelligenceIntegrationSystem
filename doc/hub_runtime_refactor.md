# IntelligenceHub 运行时重构路线图

## 目标

将当前同时承担队列、领域流程、组件构造、后台任务和查询门面的
`IntelligenceHub` 拆为：

```text
HubRuntime（标准库事件内核）
    ↑ 依赖
流程插件 / 领域插件 / 运维扩展 / Web Adapter
    ↑ 由启动组装层创建并安装
IntelligenceHubStartup
```

运行时不理解 MongoDB、AIClientCenter、VectorDB、Flask、Prompt 或任何子系统的
数据结构。事件的 `payload` 保持不透明，`subsystem` 仅作为路由标签。

## 非目标

- 不要求各子系统的 AI 输出或归档数据立即归一化。
- 不在 Hub 内决定页面应是通用标签、区块扩展还是独立页面。
- 不保留旧 `IntelligenceHub` 的构造方式或内部实现兼容层。

## 分层

| 层 | 职责 | 禁止依赖 |
| --- | --- | --- |
| runtime | 事件、队列、并发、停止、失败隔离 | 业务组件、数据库、AI、Web |
| pipeline | 收集、分析、归档阶段编排 | Flask、VectorDB、页面 |
| domain | 输入/输出校验、Prompt、评分、领域路由 | runtime 内部实现 |
| extensions | 向量、翻译、聚合、图谱、导出、实体频率 | pipeline 私有状态 |
| adapters | Mongo、AIClientCenter、Flask、配置 | 其他 adapter 的具体实现 |
| composition | 按配置安装各层 | 业务处理细节 |

## 事件边界

```text
intake.received
  → intake.accepted | intake.rejected
  → analysis.requested
  → analysis.completed | analysis.failed
  → archive.requested
  → archive.completed | archive.failed
  → extension.*
```

每个插件只能订阅事件并投递后续事件；插件之间不得直接调用。可选扩展订阅
`archive.completed`，因此关闭某项扩展不会影响主链路。

## 子系统与页面

子系统是领域插件，不是 Hub 的分支。领域插件未来可提供输入校验、Prompt、输出
处理、评分和页面贡献。Web adapter 提供通用列表/详情外壳及字段区块插槽；如需
完整定制页面，领域插件在受控 `/subsystems/<name>/...` 命名空间提供页面贡献。
两类页面可并存，Hub 不持有页面模型。

## 提交顺序

1. `HubRuntime` 与离线测试。
2. 通用流程事件、端口和插件合同。
3. Intake、Analysis、Archive 主链路插件及端到端离线测试。
4. 运行时扩展插件。
5. 应用门面和启动组装。
6. Web adapter、领域页面贡献合同。
7. 移除旧 Hub 的直连组件构造与过时文档。
