# 实体指称解析与实体图接入实施计划

> 日期：2026-10-21  
> 状态：待实施  
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
4. 节点身份依次使用 `ent_*`、`ctx_*`、`unresolved:<EntityRef>`；
5. 按最终 ID 聚合节点和关系印证。

未解析节点不得按 surface 合并。一个 EntityRef 在同篇情报的多个事件中出现时仍是同一
节点；不同情报中的两个“示威者”保持不同节点，直到被指定到同一 `ctx_*`。

图 API 给节点附加：

```text
identity_kind: global | contextual | unresolved
target_id
reference_count
editable
```

节点详情端点返回 EntityRef、原始名称、使用它的事件/角色及情报摘要，供用户判断。

## 7. 登录用户编辑流程

写接口沿用 IIS 登录保护。点击节点后先选择一个 EntityRef，然后提供两个主要入口：

### 7.1 指定到全局实体

- 搜索现有实体（精确、模糊、Wikidata 建议沿用 EntityAlias）；
- 或新建全局实体；
- 保存当前 EntityRef 到 `ent_*`；
- “登记为全局固定别名”作为单独、默认关闭的明确动作。

### 7.2 指定到上下文实体

- 搜索地点、时间和议题接近的已有 `ctx_*`；
- 或只填写一个名称快速新建；
- 高级字段折叠显示并尽量自动预填；
- 保存当前 EntityRef 到 `ctx_*`。

保存成功后局部刷新实体图。多个 ref 指向同一目标时，节点自然汇聚。第一版不提供名为
“合并节点”的普通按钮，也不默认批量处理一个聚合节点背后的所有 ref。

## 8. 迁移与灰度顺序

1. 先部署 Mongo 契约、服务和索引，但不接管图读取；
2. 停止新归档覆盖原始实体名，并给新事件投影写入 ENT ID；
3. dry-run 检查历史重建数量，再分批回填事件索引；
4. 上线只读解析预览，核对新旧图的节点数、边数和印证差异；
5. 对登录用户开放单 ref 指定和撤销；
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
- 同名泛称在未解析时保持不同节点；
- 多个 ref 指向同一目标后形成一个节点；
- 确定性别名仍能自动指向全局实体；
- 解析存储不可用时主服务与图查询安全降级。

### 9.2 集成验收场景

1. 两篇情报中的“肯尼迪”分别指定到不同人物，图上保持两个节点；
2. “特朗普”和“美国总统特朗普”分别指定到同一 `ent_*`，图上自然汇聚；
3. 两篇情报中的“示威者”初始不合并，分别指定到同一“法国养老金改革示威者”后汇聚；
4. 第三篇无关抗议中的“示威者”仍保持独立；
5. 撤销任一指定后，图恢复为独立节点；
6. 原始 `EVENT_EXTRACTION` 名称在全部操作前后保持不变。

## 10. 建议提交边界

为便于审查和回滚，实施时按以下提交拆分：

1. `feat: add entity reference and resolution store`；
2. `refactor: preserve raw entity names in event archive`；
3. `feat: retain local entity ids in event projections`；
4. `feat: resolve stable entity identities in situation graph`；
5. `feat: add entity assignment workflow to graph`；
6. `ops: add entity projection backfill and coverage report`。

