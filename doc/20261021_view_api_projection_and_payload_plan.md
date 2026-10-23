# 展示接口分级、字段投影与加载性能改造方案

日期：2026-10-21

状态：第一阶段已实施（CARD、DETAIL 读取投影及态势事件专用投影）；详情事件去重、页面拆分与缓存待实施

## 0. 正式接口分级与裁剪矩阵

以下表格是代码和评审使用的正式契约。归档 profile 的单一事实源位于
`ServiceComponent/intelligence_projections.py`；HTTP 路由不得接收或拼装任意 `fields=`。

| HTTP/调用入口 | 固定级别 | Mongo 读取 | 响应保留 | 明确裁剪 |
|---|---|---|---|---|
| `GET/POST /intelligences/query`（含具名子系统） | `card` | 卡片顶层字段 + 已批准 APPENDIX 时间/评分/模型字段 | 标题、摘要、分类、来源、显示时间、总分、Prompt、模型 | `RAW_DATA`、正文、`EVENT_EXTRACTION`、RATE、影响、建议、理由、实体速查字段、派生 `events` |
| 向量搜索 UUID 回填、聚类摘要/成员 | `card` | 与普通列表相同 | CARD；向量结果可额外带查询期 `vector_score` | 与普通列表相同 |
| `GET /api/intelligence/<uuid>`（含具名子系统） | `detail` | 完整分析展示字段、权威 `EVENT_EXTRACTION`、批准的 APPENDIX 元数据 | 正文、评分、实体、事件抽取、人工评分、显示元数据 | `RAW_DATA` 和未批准 APPENDIX 字段 |
| 归档、翻译、影子对比等服务端调用 | `internal` | 显式完整归档 | 由内部调用方处理，禁止直接序列化到 Web | 无默认裁剪；没有对应公共 HTTP 路由 |
| 地图/实体图/时间线证据卡片 | `evidence` DTO | 对应态势事件专用投影 + 归档头 | 标题、时间、评分、谓词及必要角色/对端摘要 | 正文、原始数据、完整事件抽取和无关事件字段 |

归档 Mongo 投影表：

| 字段组 | `card` | `detail` | `internal` |
|---|:---:|:---:|:---:|
| `UUID`、`INFORMANT` | ✓ | ✓ | ✓ |
| 标题、摘要、分类 | ✓ | ✓ | ✓ |
| 正文、RATE、IMPACT、TIPS、REASON | — | ✓ | ✓ |
| `EVENT_EXTRACTION`、实体速查字段 | — | ✓ | ✓ |
| 内容/发布/采集/归档时间、总分、Prompt、AI 服务/模型 | ✓ | ✓ | ✓ |
| 人工评分 | — | ✓ | ✓ |
| `APPENDIX.RAW_DATA`、内部重试/运行字段 | — | — | ✓ |
| 派生事件集合 `events` | — | 暂时保留，第二阶段移除 | 不属于归档 profile |

相关代码必须按边界集中放置：

| 边界 | 代码位置 |
|---|---|
| profile、Mongo 投影和响应 DTO | `ServiceComponent/intelligence_projections.py` |
| 归档查询执行 | `ServiceComponent/EventV4QueryEngine.py` |
| CARD/DETAIL HTTP 适配和路由公共函数 | `IntelligenceHubWebService.py` 的“情报读取接口契约”区段 |
| 地图、实体图、时间线事件投影与聚合 | `SituationViews/engine.py` |
| 态势 scoped/unscoped 路由 | `SituationViews/web.py` |

## 1. 问题与目标

当前情报列表、地域视图和实体视图已经按页面拆分，但数据读取边界仍偏向“先取完整文档，
再在 Python 或浏览器前删除不用的字段”。这会同时放大 MongoDB 读取、BSON 解码、JSON
序列化、网络传输和浏览器解析成本。

本方案的目标是：

1. 列表和证据卡片绝不读取或返回正文、`RAW_DATA`、`EVENT_EXTRACTION` 或派生事件全文；
2. 详情接口只返回详情渲染所需的一份事件表达，不同时返回权威抽取和重复派生事件；
3. 地图与实体图在 Mongo 查询阶段就使用各自需要的事件字段投影；
4. 接口按少量稳定使用场景分级，不开放任意 `fields=`，也不为每个小组件新建接口；
5. 优先解决冷加载中的大资源和大对象，避免为了节省几十字节破坏可读性或扩展性。

## 2. 现状测量

测量基于当前生产数据和本机 `127.0.0.1:5000`，窗口为最近 7 天、评分不低于 6；当时包含
684 篇文章、684 个主事件。时间只反映本机服务端，不代表远程浏览器的网络时间，但字节量和
字段冗余具有代表性。

| 项目 | 当前结果 | 主要发现 |
|---|---:|---|
| `/situation/embed/map` HTML | 105,009 B | 同一模板包含地图、实体图和实体编辑器的全部 DOM/JS |
| `d3.min.js` | 279,706 B | 地图与实体图共用，已有 7 天缓存 |
| `topojson-client.min.js` | 7,169 B | 只有地图需要，实体图页面也会加载 |
| `countries-110m.json` | 107,761 B | 地图边界数据，合理 |
| `countries.json` | 1,408,911 B | 前端只使用 `cca2`、`latlng` 和中文/通用名称 |
| `countries.json` 必要字段离线投影 | 约 14,278 B | 可缩小约 99%，且不需要运行时接口 |
| `/situation/api/scoped/map` | 22,750 B / 31 ms | 响应不大，但仍含前端未使用字段 |
| 地图 API 内部事件读取 | 953,670 B | 读取 684 个完整事件文档 |
| 地图事件必要投影 | 206,536 B | 预计减少约 78% 的事件 BSON 读取 |
| `/situation/api/scoped/entity-graph` | 768,599 B / 96 ms | 1119 节点、953 边；大部分字段确实参与图交互 |
| 实体图事件必要投影 | 476,012 B | 相比完整事件读取约减少 50% |
| `/intelligences/query` 10 条 | 51,265 B / 9 ms | 返回了卡片不用的 `EVENT_EXTRACTION`、RATE、TIPS 等 |
| 列表 Mongo 读取 10 条 | 106,480 B | 还读取了随后才删除的 `APPENDIX.RAW_DATA` 和正文 |
| 列表卡片必要 Mongo 投影 | 8,397 B | 预计减少约 92% 的 BSON 读取 |
| 详情样本 | 9,477 B | 同时返回 `EVENT_EXTRACTION` 和由事件集合读取的 `events` |

按当前样本，仅删除响应中确定未消费的字段，JSON 体积估算为：

- 地图聚合：约减少 24%；
- 列表 10 条：约减少 78%；
- 详情去掉重复 `events`：约减少 35%；
- 实体图：只减少约 3%，说明其主要成本是数据规模和浏览器力导向布局，而非明显的字段浪费。

因此“地图加载慢”至少有三类来源，不能只靠裁 API JSON 解决：

1. 冷加载下载 1.41 MB 的完整国家资料；
2. 105 KB 的嵌入页重复携带另一视图和编辑器代码；
3. 实体图在高上限配置下返回近 0.8 MB 数据，并在浏览器运行千节点力导向布局。

### 2.1 第一阶段实施后复测（2026-10-23）

使用同一生产 MongoDB 做只读 BSON 测量，未重启或修改数据库：

| 项目 | 完整读取 | 固定投影 | 降幅 |
|---|---:|---:|---:|
| 最新 10 条列表归档 | 77,586 B | 7,764 B | 90.0% |
| 最新 10 条 CARD JSON DTO | — | 8,118 B | 已低于 15 KB 目标 |
| 最近 7 天地图事件（994 条） | 1,376,894 B | 300,233 B | 78.2% |
| 最近 7 天实体图事件（994 条） | 1,376,894 B | 682,444 B | 50.4% |
| 最近 7 天实体时间线事件（994 条） | 1,376,894 B | 754,072 B | 45.2% |

这组结果验证了 Mongo 投影本身已经达到第 9 节的列表、地图和实体图读取降幅目标。它不代表
浏览器最终耗时；实体图仍可能主要受响应规模和力导向布局影响，需在服务重启后继续记录
`perf.log` 中的 CARD 响应字节与 p50/p95。

## 3. 改造前边界的问题（基线记录）

### 3.1 情报列表是事后删字段

实施前 `EventV4QueryEngine.query_intelligence()` 使用 `{"_id": 0}`，MongoDB 会返回完整归档
文档。`archive_public_view()` 去掉 `APPENDIX` 后，Web 层的 `exclude_raw_data()` 再删除
`EVENT_TEXT` 和 `events`，但 `RAW_DATA`、正文和 `EVENT_EXTRACTION` 已经完成数据库传输与
反序列化，`EVENT_EXTRACTION` 最终仍进入列表响应。

向量列表也曾有相同问题：向量召回 UUID 后调用完整 `get_intelligence()`，最后才清理。
第一阶段已将普通列表、向量回填和聚类卡片统一切换为 `card`。

### 3.2 详情返回两份事件表达

`/api/intelligence/<uuid>` 返回权威 `EVENT_EXTRACTION`，同时 `_enrich()` 又查询
`v4_events` 并加入 `events`。详情渲染器优先使用 `events`，但已经具备从
`EVENT_EXTRACTION` 构造展示事件的回退逻辑。两份结构会增加读取和传输，也带来二者短暂
不一致时的语义风险。

### 3.3 态势接口共用完整事件读取

实施前 `SituationViewEngine._load_events()` 对归档头部已经做了合理投影，但对事件集合使用
`{"_id": 0}`。地图只需要地点和谓词；实体图需要角色、关系拓扑和极性限定。时间、属性、
事件间关系、metadata 等字段不应为这些聚合一并读取。

第一阶段已按地图、实体图和实体时间线拆成固定事件投影。地图/实体证据列表返回的文章对象
本身已是摘要，不包含 `RAW_DATA` 或
`EVENT_EXTRACTION`；其主要问题是每次点击都会重新加载并扫描整个窗口的事件，而不是响应
对象过大。

### 3.4 前端静态资源不是最小集合

地图真正使用的国家元数据只有国家代码、中心点和显示名，却下载完整国家资料；地图和实体图
还共用一份巨大的内联模板。静态资源没有观察到服务进程直接提供 gzip/br 压缩；生产反向代理
是否压缩需要单独确认，不能把压缩当成字段投影的替代品。

## 4. 分级契约

只定义四种稳定层级。层级由服务端用枚举或专用方法选择，不允许调用方传任意字段名。

### 4.1 `card`：情报列表卡片

适用：普通列表、搜索结果、向量搜索结果、聚类成员等列表型界面。

固定字段：

```text
UUID, INFORMANT,
EVENT_TITLE, EVENT_BRIEF,
TAXONOMY, SUB_CATEGORY,
content_time, content_time_source,
published_at, collected_at, archived_at,
total_score, prompt_version, ai_service, ai_model,
vector_score（仅向量结果）
```

明确禁止：

```text
APPENDIX, RAW_DATA, EVENT_TEXT, EVENT_EXTRACTION, events,
RATE, IMPACT, TIPS, REASON, TIME, LOCATION, PEOPLE, ORGANIZATION
```

当前新闻 UI 插件只提供样式，不额外消费字段。以后某个子系统确需卡片扩展字段时，应在服务端
子系统注册信息中声明允许的 `card_projection`，而不是让浏览器插件请求任意 Mongo 路径。
`APPENDIX.RAW_DATA` 永远不允许进入卡片扩展。

### 4.2 `detail`：单篇情报详情

适用：独立详情页和列表弹窗。保留完整分析展示字段、`EVENT_TEXT`、`RATE`、人工评分及
`EVENT_EXTRACTION`；`APPENDIX` 仍只投影为经过批准的扁平元数据。

详情默认不再查询或返回重复的派生 `events`。`EVENT_EXTRACTION` 是权威契约，前端使用已有
转换逻辑渲染。实施前必须补齐并验证 `role_semantics` 等展示差异；若确有仅存在于派生事件的
展示字段，应在服务端从权威抽取确定性生成，而不是再返回整份事件索引文档。

### 4.3 `evidence`：地图/实体图的情报证据卡片

适用：地图热点和实体节点/边下方的情报列表。统一基础 DTO：

```text
uuid, title, score, content_time, event_count,
predicates: [{label, count}]
```

实体图可增加同一 DTO 的可选 `counterparts`、`roles`；地图不再返回未展示的
`dominant_category`、完整类别分布和地点数组。分页元数据只保留：

```text
total, offset, limit, has_more
```

这是一个 DTO 的可选扩展，不再拆成多个细碎接口。

### 4.4 `internal`：内部完整记录

只供归档、维护、导出、调试等可信服务端代码使用。是否包含 `APPENDIX` 必须显式指定；Web
路由不得把 `internal` profile 直接序列化。现有 `light_weight`、`include_appendix` 两个布尔
参数应逐步由明确 profile 替代，兼容壳仅在迁移期保留。

## 5. 态势接口的专用投影

态势聚合继续使用现有少量专用端点，不并入通用情报查询，也不为每个图表控件新增接口。

### 5.1 地图事件投影

Mongo 只读取：

```text
event_uuid, intelligence_uuid, predicate, event_location, is_primary
```

地图响应热点保留前端实际使用的：

```text
country_code, event_count, max_score, dominant_category,
categories, predicates, locations
```

顶层不再重复返回 `categories` 词典（页面模板已经注入同一词典）；meta 只保留
`mapped_event_count`、`location_coverage`、`hotspot_count`、`event_count`、`truncated`。

### 5.2 实体图事件投影

Mongo 只读取：

```text
event_uuid, intelligence_uuid,
frame.topology, predicate, roles,
qualifiers.type, qualifiers.value,
is_primary
```

实体解析需要 `roles` 中的 `local_entity_id/name/type/country_code`，不能进一步只保留名称。
图响应中节点的 `target_id` 当前未被前端使用，可以删除；其余节点与边字段用于搜索、详情、
提示、绘图或编辑，不应为了数字好看而删除。meta 只保留界面消费的统计值和 `truncated`。

实体图响应的根本上限仍由 `max_nodes/max_edges` 控制。若千节点布局继续成为瓶颈，应另做图
规模分层或服务端布局，不能把“删字段”误当成布局优化。

### 5.3 实体时间线事件投影

实体时间线与实体图共用身份和关系字段，但只有时间线额外读取抽取时间：

```text
event_uuid, intelligence_uuid,
frame.topology, predicate, roles,
qualifiers.type, qualifiers.value,
time,
is_primary
```

实体搜索、聚合时间线和分轨时间线共用这一投影。来源域名来自已经很小的归档头投影，不读取
正文或 `EVENT_EXTRACTION`。时间线按指定实体定向生成，不受实体图 `max_nodes/max_edges` 裁剪。

### 5.4 窗口数据复用

地图热点、点击后的地图证据、实体图及实体证据目前会各自执行窗口读取。实施字段投影后再测量
数据库耗时；若重复扫描仍显著，可为固定 scoped 查询增加短 TTL 的窗口快照缓存。缓存键中的
结束时间应按小时间桶归一化，否则每次请求的 `now` 都不同，缓存永远无法命中。缓存失败必须
回退到直接查询，不能影响 IIS 主链。

## 6. 静态资源与页面拆分

### 6.1 第一优先：紧凑国家元数据

新增由仓库脚本离线生成并纳入版本管理的紧凑资产，例如
`countries-map-meta.json`，只含：

```json
{"cca2":"CN","latlng":[35,105],"name":"中国"}
```

运行时不再下载完整 `countries.json`。生成脚本应排序并校验 ISO 代码唯一性，避免手工维护。

### 6.2 第二优先：按视图加载代码

将当前 105 KB 内联模板拆为：

- 很小的公共壳与公共样式/工具；
- 仅地图页面加载的 map bundle 和 TopoJSON；
- 仅实体页面加载的 graph/editor bundle；
- 可缓存的静态 JS/CSS，而不是每次响应重复内联。

`IntelligenceViews` 现有 iframe 按 Tab 懒加载机制保留，不同时预加载四个视图。

### 6.3 压缩

反向代理应为 JSON、JS、CSS、SVG/TopoJSON 开启 gzip 或 brotli，并保留 ETag/缓存头。压缩是
传输优化，数据库投影仍必须实施；否则服务端仍会承担完整 BSON 读取和 JSON 构造成本。

## 7. 实施顺序

### 阶段 A：观测与契约测试

1. 为列表、详情、地图、实体图和证据接口记录总耗时、Mongo 耗时、结果数量和响应字节数；（列表已记录 profile、总耗时、数量和响应字节；其余及 Mongo 分段待补）
2. 日志只记录数字和 profile，不记录情报正文；（列表已实施）
3. 增加字段白名单契约测试，明确禁止列表出现 `RAW_DATA/EVENT_TEXT/EVENT_EXTRACTION/events`；（已实施）
4. 记录改造前基线，避免只凭主观感受判断。（已记录于第 2 节）

### 阶段 B：高收益、低风险改造

1. 生成紧凑国家元数据并切换地图；（待实施）
2. 新增集中式 `card/detail/internal` 投影定义和 DTO 构造器；（已实施）
3. 普通、向量与聚类列表统一使用 `card`，在 Mongo 查询阶段投影；（已实施）
4. 地图/实体图/实体时间线分别使用专用事件投影；（已实施）
5. 精简地图、图、时间线和 evidence 的 meta/未消费字段。（已实施）

### 阶段 C：详情去重与页面资源拆分

1. 让详情只使用权威 `EVENT_EXTRACTION`，验证事件展示和人工评分无回归；
2. 移除详情的派生事件额外查询和 `events` 响应；
3. 拆分地图、实体图 bundle，使静态资源可独立缓存；
4. 配置并验证生产反向代理压缩。

### 阶段 D：按测量决定是否缓存

只有阶段 B/C 后 Mongo 扫描仍是主要耗时时，才引入固定窗口短 TTL 缓存。实体图布局若仍慢，
单独评估默认规模、渐进渲染或服务端布局，不与接口字段改造捆绑。

## 8. 兼容与迁移

- 不开放任意字段选择，避免泄漏 `APPENDIX` 或形成无法维护的字段组合；
- 内置前端先显式请求/调用 `card` profile；旧查询行为可保留一个迁移周期，并记录调用量；
- 确认没有外部调用方依赖列表中的详情字段后，再将 `card` 设为列表默认；
- 单篇详情 URL 和态势 URL 不变，只收紧各自既有用途下的响应契约；
- `archive_public_view()` 可继续作为安全清理边界，但不能再替代 Mongo 投影；
- 所有子系统共享投影基础设施，子系统只能通过服务端白名单增加卡片字段。

## 9. 验收标准

### 正确性

- 列表、搜索、向量结果的现有卡片内容和排序不变；
- 详情的正文、评分、事件、实体和人工评分功能不变；
- 地图热点、地点详情和证据数量不变；
- 实体图节点、边、搜索、编辑和证据列表不变；
- 可选子系统或投影构造失败时，不影响采集、分析和归档主链。

### 性能与载荷

- 10 条列表响应不含禁用字段，目标小于 15 KB；
- 列表 Mongo 读取字节量相对当前样本减少至少 80%；
- 地图不再请求完整 `countries.json`，国家元数据目标小于 25 KB；
- 地图事件 BSON 读取相对当前样本减少至少 70%；
- 实体图事件 BSON 读取相对当前样本减少至少 40%；
- 所有大响应在生产入口确认启用压缩；
- 用 p50/p95 及冷/热缓存分别比较，不能只记录一次本机请求。

## 10. 本轮明确不做

- 不改变 Event V4 归档结构；
- 不删除 `APPENDIX.RAW_DATA` 的存储；
- 不把地图或实体图数据写回权威归档；
- 不建立任意 GraphQL/字段选择协议；
- 不为了减小响应而删除实体搜索、原文表述、证据定位所需字段；
- 不在缺少测量时直接引入 Redis 或复杂多级缓存。
