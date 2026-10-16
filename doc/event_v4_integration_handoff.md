# Event V4 生产契约

更新时间：2026-10-11

## 当前结论

IIS 生产主链使用 Event V4，不兼容或双读 V1/V2 存量文档。V4 延续 V2 的扁平顶层，
只在分析结果中加入 `EVENT_EXTRACTION`；没有 `analysis`、`message`、`classification`、
`assessment` 或 `kind` 包装层。

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

生产 Prompt 当前版本为 `v41`。它明确要求六维 `RATE`、V2 扁平顶层，并从
Event Engine 的 Registry/Prompt 生成器注入事件字段、Frame、角色、时间和关系规则。

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
