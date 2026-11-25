# EntityAlias V2 转正首版记录（2026-11-25）

## 背景与结论

实体图中的“普京 / 弗拉基米尔·普京”和“特朗普 / 唐纳德·特朗普”没有合并，不是图渲染问题，
而是 V1 事实库为空、V2 又只运行在 shadow。V2 已有对应唯一 Wikidata 候选，但所有补录实体仍是
`unverified`，Wikidata aliases 又统一标记为 `context_required`，因此旧策略必然弃权。

本次采用可回滚转正，不把所有 V2 候选批量提升为 verified，也不改写历史 Event V4 归档。
EntityResolution 在读图时使用 V2 解析，同一 `ent2_*` 目标自然聚合历史指称。

## 已实现

1. 增加 `entity_alias_v2.mode`：`shadow`、`prefer_v2`、`v2_only`。
2. V2 事实服务由正式适配器和 shadow 扩展共用，避免同一数据库重复实例。
3. EntityResolution 向 registry 传递完整请求，包括类型和 `country_code`。
4. 分离实体信任与名称绑定：
   - 唯一、类型匹配、带 Wikidata QID 的直接名称可自动绑定；
   - 唯一、带 QID、长度至少 2、带国家上下文的人物简称可自动绑定；
   - 组织/地点的上下文别名、同名候选、类型冲突继续弃权。
5. `prefer_v2` 未命中时仍回退 V1，随时可改回 `shadow`。

## 当前限制

- 国家码当前作为“该次抽取具有地域上下文”的门槛，尚未与 Wikidata 的国籍/所属国家 claim
  做双向校验；因此首版只开放人物，不开放组织和地点。
- 中文显示名仍沿用 V2 canonical；特朗普的 Wikidata canonical 可能显示为繁体“唐納·川普”。
  身份已经合并，显示语言偏好需要独立治理，不能用展示问题反向改变实体 ID。
- V2 尚未接管查询别名展开和管理页面；这两项在 `prefer_v2` 观察稳定后再迁移。
- 影子库已有历史观测不包含足够的结构化上下文，暂不据此批量提升实体或别名状态。

## 观察与后续调整

重点记录以下样本，按具体问题调整，不先扩张通用规则：

- 人物简称误合并或漏合并；
- 同一简称在不同国家、时代或职业中的冲突；
- `country_code` 缺失导致的漏合并比例；
- canonical 中文语言/地区偏好；
- 有足够证据后，是否将特定别名从 contextual 提升为 direct；
- `prefer_v2` 稳定后，查询展开、管理 UI 和最终 `v2_only` 的迁移时点。

回退只需把 `optional_subsystems.entity_alias_v2.mode` 改为 `shadow` 并重载服务；V2 事实库和
shadow 观测都保留，不丢失数据。
