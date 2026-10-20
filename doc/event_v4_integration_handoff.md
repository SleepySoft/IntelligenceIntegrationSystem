# Event V4 生产契约

更新时间：2026-10-20

## 当前结论

IIS 生产主链使用 Event V4，不兼容或双读 V1/V2 存量文档。V4 延续 V2 的扁平顶层，
只在分析结果中加入 `EVENT_EXTRACTION`；没有 `analysis`、`message`、`classification`、
`assessment` 或 `kind` 包装层。

这里有两条彼此独立的版本轴：`Event V4` 是数据契约和处理主链的大版本，`Prompt vNN`
是该契约之下的 Prompt 迭代编号。因此当前灰度应表述为“Event V4 / Prompt v50”，
而不是“系统 v50”或“Event V50”。Prompt v50 仍输出并接受 Event V4 的同一协议，
编号增长不表示存储结构升级。

```text
CollectedData
→ AnalysisResultV4（V2 顶层字段 + EVENT_EXTRACTION）
→ ArchivedDataV4（分析结果 + 身份 + 派生速查字段 + APPENDIX）
→ 可选的 v4_events 派生查询索引
```

## 分析结果

`AnalysisResultV4` 的顶层字段为：

- `TAXONOMY`、`SUB_CATEGORY`、`REASON`
- `EVENT_TITLE`、`EVENT_BRIEF`、`EVENT_TEXT`
- `IMPACT`、`RATE`、`TIPS`
- `EVENT_EXTRACTION`

低价值结果由 `TAXONOMY == "NonIntelligence"` 表示，不另设结果类型。模型不输出
`TIME`、`LOCATION`、`PEOPLE`、`ORGANIZATION`；这些字段在归档时从
`EVENT_EXTRACTION` 派生。`GEOGRAPHY` 已删除。

生产 Prompt 当前由 `v48` 和 `v50` 同时等概率抽样：`v48` 作为对照组，`v50`
作为角色约束修复灰度组。两者均使用固定 system + 动态 user，模型输出核心事件子集、六维
RATE 等级词，时间只输出 normalized 与 surface；服务端桥接为完整事件契约和
数字评分。低价值输出另需 `NON_INTELLIGENCE_REASON` 原因码。归档中的
`APPENDIX.__PROMPT_VERSION__` 可用于分组评估。

`v49` 是独立构建的精简版本，完整保留 Registry 目录及评级规则；首轮灰度
发现角色白名单/实体类型遵循率下降，现保留为 `sampling=False` 的对照。`v50` 不改
输出协议，在 v49 精简结构上增加 Registry 驱动的角色白名单和实体类型输出前检查。
详见 [v49 精简记录](20261020_prompt_v49_compaction.md) 与
[v50 夜间灰度记录](20261020_prompt_v50_role_guard_gray.md)。

## 归档文档

`ArchivedDataV4` 直接继承/组合分析字段，额外包含：

- `UUID`：文章业务 ID，与 MongoDB `_id` 无关。
- `INFORMANT`：来源去重键。
- `TIME`、`LOCATION`、`PEOPLE`、`ORGANIZATION`：可重建的速查字段。
- `APPENDIX`：系统元数据容器。

归档文档内嵌的 `EVENT_EXTRACTION` 是权威事件数据。Mongo `_id` 不进入业务模型，
查询边界读取后必须清除。基本查询和前端响应默认移除 `APPENDIX`，只按需投影主时间、
总分等显示字段。

`APPENDIX` 的全部键名及 Mongo 路径统一定义于
`ServiceComponent/intelligence_contracts.py`，不得在业务模块重复声明。其中
`RAW_DATA` 保存清洗后的采集快照；主时间、归档时间、评分、模型、Prompt、翻译修订、
人工评分和 schema 版本也全部位于该容器。

## 派生事件索引

每个子系统使用以下集合：

- `v4_archived`：权威高价值归档。
- `v4_low_value`：低价值归档。
- `v4_events`：可由 `EVENT_EXTRACTION` 重建的查询索引。
- `v4_outbox`：派生事件索引的重试队列。
- `cached`：采集输入；处理终态使用 `APPENDIX.__ARCHIVED__`。

不创建 `v4_entities`。事件索引的角色、地点及 qualifier 来源直接保存
`name`、`type`、可选 `country_code` 快照，不保存实体 UUID。文章归档先独立提交；
派生事件索引失败只留下 pending outbox，不得回滚或阻塞权威归档。

## 时间与查询

内容主时间优先采用带有效时刻的发布时间；零点发布时间视为仅有日期，回退采集时间；
最后回退归档时间。存储路径位于 `APPENDIX`，公共查询响应仅输出扁平显示字段。
普通列表默认按归档时间倒序，使新完成分析的记录立即出现在前面；也允许切换为内容主时间
倒序。时间范围筛选仍使用内容主时间，不因列表排序方式改变。列表同时投影发布、采集和
归档时间，并突出显示当前排序所采用的时间；向量检索保持按相似度排序。

验证命令：

```bash
pytest -q Test
```
