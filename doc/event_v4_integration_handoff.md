# Event V4 生产状态

更新时间：2026-10-09

## 当前结论

IIS 生产主链和所有已启用子系统已统一切换到 Event V4，不保留 V1/V2 兼容、双读或字段回退。
旧实现、Prompt、测试、训练资料和设计文档位于 `recycled/legacy_intelligence/`，不会被生产代码加载。

## 主链契约

```text
CollectedDataV4
→ EventV4PipelinePorts
→ ValuableIntelligenceV4 | NonIntelligenceV4
→ EventRecord 转换与实体解析
→ ArchivedIntelligenceV4 + events + entities
```

- `prompts_event_v4.py` 组合 IIS 外层分析要求与 `event_engine` Registry 生成的事件抽取章节。
- `event_engine` Registry 是谓词、Frame、角色和语义校验的唯一规则源。
- 情报、事件、实体和 outbox 使用每个子系统独立的 MongoDB 集合。
- 多集合提交采用可重放 outbox；启动时执行 `recover_pending()`。
- 人工调试使用同一个 `EventV4PipelinePorts`，可选择版本、查看 Prompt、覆盖 Prompt，并保持无业务库写入。

## V4 字段约定

- 标识：`intelligence_uuid`（Mongo `_id` 使用同值）
- 标题/摘要/正文：`analysis.message.title|brief|text`
- 分类：`analysis.classification.taxonomy|subcategories`
- 评估：`analysis.assessment`
- 事件抽取：`analysis.event_extraction`
- 总分：`total_score`
- 原始数据：`raw_data`
- 归档时间：`archived_at`
- 翻译修订：`translation_revision`
- 人工评分：`manual_rating`

查询、统计、实体频率、翻译、向量化、聚合、动态图谱、RSS、导出和 Web 卡片均使用上述字段。

## MongoDB 集合

每个子系统以 `collection_prefix` 为前缀创建：

- `v4_archived`
- `v4_low_value`
- `v4_events`
- `v4_entities`
- `v4_outbox`
- `cached`（采集输入；处理终态沿用运行协议 `APPENDIX.__ARCHIVED__`）

## 尚未纳入本次切换

- CanonicalEvent 自动归并仍保持关闭，待实体消歧、候选召回和并发 CAS 完成后启用。
- 复杂实体别名以及人工合并/拆分入口仍需单独实现。

## 验证

根项目活动测试集：

```bash
pytest -q Test
```

V1/V2 历史测试不属于活动测试集。
