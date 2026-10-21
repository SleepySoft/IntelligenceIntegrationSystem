# 实体指称解析与实体图接入实施计划

> 日期：2026-10-21  
> 状态：MVP 已实施（2026-10-21）
> 设计依据：`doc/20261021_entity_identity_resolution_and_wiki_design.md`

## 1. 本轮目标与非目标

本轮目标是建立最小但完整的人工实体解析闭环：登录用户从实体图选择一个情报实体，指定
到全局实体 `ent_*` 或上下文实体 `ctx_*`，图根据稳定 ID 自然汇聚，并可撤销决定。

本轮不实现：

- AI 自动消歧；
- 自动把相似泛称合并；
- 完整实体 Wiki；
- 全局实体之间的高级合并/拆分；
- UsageRef 级纠正（只预留协议）；
- 复杂的上下文实体必填元数据。

## 2. 模块边界

保留 `EntityAlias/` 作为全局确定性实体与名称注册表，不把情报相关记录写入其 JSONL。
新增相对独立的 `EntityResolution/` 包：

```text
EntityResolution/
  model.py            EntityRef、UsageRef、解析记录、上下文实体契约
  store.py            MongoDB 集合与索引
  service.py          指定、批量解析、撤销、反向查询
  iis_integration.py  IIS 组合根装配与跨子系统注入
```

`EntityResolution` 只通过一个小接口查询全局实体，不直接依赖 `SituationViews`；
`SituationViews` 通过注入的 resolver 使用它。这样其他使用 AI Client/Event V4 的项目也能
复用解析基础设施。

## 3. 数据契约

### 3.1 EntityRef

```text
subsystem:          子系统名
intelligence_uuid:  情报 UUID
local_entity_id:    EVENT_EXTRACTION 中的 ENT ID
```

三字段构成唯一键。MVP 使用 Mongo 复合唯一索引，不强制额外生成 UUID。协议预留可选
UsageRef：`event_uuid + role + position`，但第一阶段服务只处理 EntityRef。

### 3.2 MongoDB 集合

沿用子系统集合前缀，使解析数据与情报生命周期、干跑数据隔离：

```text
<prefix>entity_global_resolutions
<prefix>contextual_entities
<prefix>contextual_entity_resolutions
<prefix>entity_resolution_audit
```

歧义专名到 `ent_*` 与泛称到 `ctx_*` 物理分开。每个 EntityRef 在任一时刻最多有一个有效
目标；服务层负责跨两个解析集合检查冲突。

上下文实体 MVP 必填字段只有：

```text
id、name、created_at、created_by
```

预留但不要求用户输入：`type`、`countries`、`locations`、`time_range`、`topic`、
`description`。界面可从当前情报自动预填，所有值以后均可编辑。

### 3.3 审计与撤销

每次指定生成 `decision_id`。当前解析记录保存 `decision_id`，审计集合保存动作、旧目标、
新目标、操作者、时间和可选理由。撤销按 `decision_id` 恢复前一状态，接口必须幂等。

## 4. 事件数据准备

### 4.1 停止破坏性名称替换

移除组合根对归档管线 `_entity_name_normalizer` 的注入，或把它改为不修改权威抽取。
`EVENT_EXTRACTION.entities[].name` 从此保留模型输出。确定性别名在解析视图中生效。

### 4.2 派生事件保留 ENT ID

修改 `ServiceComponent/event_v4_conversion.py`，使角色、地点和限定词中的实体快照增加：

```json
{"local_entity_id": "ENT1", "name": "肯尼迪", "type": "person"}
```

该字段属于可重建事件索引，不改变 Event V4 权威抽取协议。

### 4.3 历史回填

提供独立脚本从归档的 `EVENT_EXTRACTION` 重建事件索引，支持：

- 子系统选择；
- dry-run；
- 分页和断点；
- 只处理缺少 `local_entity_id` 的记录；
- 数量、失败原因和覆盖率报告。

在回填覆盖率达到要求前，实体图保持旧实现；不为缺失 ENT ID 的旧投影生成可写的临时 ref，
避免人工决定绑定到不稳定位置。

## 5. 解析服务

实现以下最小接口：

```text
resolve_many(entity_refs)
assign_global(entity_ref, entity_id, actor, reason="")
create_contextual(name, optional_fields, actor)
assign_contextual(entity_ref, contextual_entity_id, actor, reason="")
unassign(entity_ref, actor, reason="")
list_references(target_id)
search_contextual(query, context_hints)
undo(decision_id, actor)
```

写操作必须验证：

- ref 对应的情报和 `local_entity_id` 确实存在；
- `ent_*` 在 EntityAlias 注册表中存在；
- `ctx_*` 在当前子系统的上下文实体集合中存在；
- 同一 ref 不会同时指向全局实体和上下文实体；
- 重复请求幂等，不产生重复审计。

全局确定性别名登记继续调用 `EntityAliasService`，作为明确的独立操作，不能在普通
`assign_global` 中隐式发生。

## 6. 实体图读取接入

`SituationViewEngine` 增加可选 resolver 端口。一次图查询的处理顺序为：

1. 收集窗口内事件涉及的全部 EntityRef；
2. 一次 Mongo 查询批量获取 ref 解析；
3. 对未命中 ref 的名称批量查询确定性 EntityAlias；
4. 节点身份依次使用 `ent_*`、`ctx_*`，未解析时回退到“名称 + 类型 + 国家”的临时投影；
5. 按最终 ID 聚合节点和关系印证。

名称投影只是方便阅读和形成连通图的显示层假设，不写入固定别名或解析旁表。同名误合并时，
用户从聚合节点中选择具体 EntityRef，分别指定到更明确的 `ent_*` 或 `ctx_*`，即可自然拆分。

图 API 给节点附加：

```text
identity_kind: global | contextual | surface
target_id
reference_count
editable
```

节点详情端点返回 EntityRef、原始名称、使用它的事件/角色及情报摘要，供用户判断。

## 7. 登录用户编辑流程

写接口沿用 IIS 登录保护。登记和消歧在同一编辑器中连续操作，但不是互斥模式：

### 7.1 登记或选择目标实体

- 用 radio 选择录入全局实体表 `ent_*` 或语境实体表 `ctx_*`；
- 可搜索已有目标，也可登记并选择一个新目标；
- 新建目标本身不提交 EntityRef、不创建情报关联；
- 选择全局库时，可另行把当前名称登记为固定别名，只提交名称与目标 `ent_*`；
- 用户可以到此结束，只登记实体而不执行消歧。

### 7.2 按情报消歧

- 多选一个或多个 EntityRef；
- 将它们批量指向上一步选中的 `ent_*` 或 `ctx_*`；
- 可以指向已有目标，也可以登记新目标后立即消歧；
- 指向全局实体不隐式登记固定别名；
- 允许以后改派、清除或撤销。

保存成功后提示用户按需手动刷新实体图，避免大型图反复重绘。多个 ref 指向同一目标时，节点自然汇聚。第一版不提供名为
“合并节点”的普通按钮，也不默认批量处理一个聚合节点背后的所有 ref。

## 8. 迁移与灰度顺序

1. 先部署 Mongo 契约、服务和索引，但不接管图读取；
2. 停止新归档覆盖原始实体名，并给新事件投影写入 ENT ID；
3. dry-run 检查历史重建数量，再分批回填事件索引；
4. 上线只读解析预览，核对新旧图的节点数、边数和印证差异；
5. 对登录用户开放多 ref 选择、批量指定/清除和单 ref 撤销；
6. 小窗口灰度启用 ID 图投影；
7. 确认性能与结果后成为默认路径，保留可快速关闭的配置开关。

任何解析服务、Mongo 集合或 EntityAlias 初始化失败时，IIS 主服务必须继续运行；图可以降级
为只读旧投影，但不得将不稳定的临时引用写入数据库。

## 9. 测试与验收

### 9.1 单元测试

- EntityRef 唯一性、序列化和校验；
- 全局/上下文解析互斥；
- 指定、改派、取消和撤销幂等；
- 上下文实体只有名称时也能创建；
- 无效 `ent_*`、`ctx_*` 和 ref 被拒绝；
- 同名未解析指称先按名称临时连接，人工指定后可拆分；
- 多个 ref 指向同一目标后形成一个节点；
- 确定性别名仍能自动指向全局实体；
- 解析存储不可用时主服务与图查询安全降级。

### 9.2 集成验收场景

1. 两篇情报中的“肯尼迪”初始按名称连接，分别指定到不同人物后拆成两个节点；
2. “特朗普”和“美国总统特朗普”分别指定到同一 `ent_*`，图上自然汇聚；
3. 两篇情报中的“示威者”初始按名称连接，分别指定到同一“法国养老金改革示威者”后改用稳定身份汇聚；
4. 第三篇无关抗议中的“示威者”可被单独指定到另一语境实体，从原名称节点拆出；
5. 撤销指定后，该 ref 恢复参与名称临时连接；
6. 原始 `EVENT_EXTRACTION` 名称在全部操作前后保持不变。

## 10. 建议提交边界

为便于审查和回滚，实施时按以下提交拆分：

1. `feat: add entity reference and resolution store`；
2. `refactor: preserve raw entity names in event archive`；
3. `feat: retain local entity ids in event projections`；
4. `feat: resolve stable entity identities in situation graph`；
5. `feat: add entity assignment workflow to graph`；
6. `ops: add entity projection backfill and coverage report`。

## 11. 实施结果

本轮已完成上述 MVP 闭环：

- 新增 `EntityResolution/`，以 MongoDB 旁表分别保存全局实体解析、语境实体与审计记录；
- 权威 `EVENT_EXTRACTION` 不再被确定性别名后处理改写；
- 派生事件实体快照保留文章内稳定 `ENT*` ID，并提供 dry-run/可重复执行的历史重建脚本；
- 实体图按 `ent_*`、`ctx_*`、带情报作用域的未解析 ref 聚合，解析服务异常时安全降级；
- 登录用户用 radio 选择目标录入全局或语境表；登记与消歧是两个独立按钮而非互斥模式；
- 消歧可多选情报实体引用，使用已有目标或登记新目标后批量指定；单条选择时可撤销；
- 固定别名使用独立全局登记，只修改 `EntityAlias`，不接收情报 UUID，不写解析旁表；
- 网络规模设为 1 时可人工查看并整理单次未解析关系，不再提供语义重复的专用开关；
- Wiki 暂不建立本地事实库，只为全局标准名提供百度百科与中文 Wikipedia 搜索跳转。

尚未启用的预留能力仍包括 UsageRef 级拆分、AI 自动消歧和实体重定向。历史索引重建脚本
通过跳过已带完整 ENT ID 的文章实现可重复续跑，而不是维护额外游标。

