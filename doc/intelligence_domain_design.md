# Event V4 情报域设计

本文只描述 **Event V4 同构情报域**：所有启用的情报域共享 Event V4 数据契约和 Prompt
机制，但拥有独立的数据集合、URL 前缀、AI 客户端分组、评分配置和可选 UI 插件。

这里的 `news`、`finance`、`industry` 称为“情报域”，不要与配置中的
`optional_subsystems` 混淆；后者指 translation、vector、situation 等可整体启停的外围
能力。自定义 Prompt、自定义 Schema 和按情报域启用地域/实体视图的现状与改造方案见
[IIS 情报域扩展能力调查与重构建议](20261016_intelligence_domain_extensibility_audit.md)。

## 配置

当前情报域入口位于 `_config/iis.yaml` 的 `hub.domains`，详细参数分离在
`_config/domains/`：

```yaml
hub:
  domains:
    default: news
    enabled:
      news: 国际新闻
      finance: 财经情报
```

详细配置支持：`collection_prefix`、`url_prefix`、`ai_client_group`、`scoring`、`ui_plugin`
和 `detail_page`。每个领域通过 `factory` 注册自己的 Prompt Catalog、结果校验、评分和存储；
`finance`、`industry` 尚无独立实现时可显式复用 `news` 工厂。人工调试可按领域选择 Prompt，
也可在单次请求中覆盖 Prompt。

## 资源隔离

`DomainResourceRegistry` 为每个情报域构造独立的 cache、V4 intelligence、low-value、event、entity
和 outbox 集合，并注册 `EventV4QueryEngine`、统计引擎、实体解析器和归档仓库。

默认情报域挂载根 URL；其他情报域通常挂载 `/{name}`。UI 插件入口为
`_config/domains/{name}/ui_plugin.js`，插件接收的文档只使用该领域归档 DTO。

## 新增同构情报域

1. 在主配置 `hub.domains.enabled` 中登记名称和显示名。
2. 设置唯一 `collection_prefix` 与 `url_prefix`。
3. 如有需要，设置 AI 客户端分组和 V4 评分权重。
4. 如需定制展示，新增读取 V4 DTO 的 `ui_plugin.js`。
5. 使用人工情报调试页验证正式 Prompt 拼接、覆盖 Prompt 和 V4 校验结果。

若要创建异构情报域，必须另外实现并注册领域工厂；仅增加 `schema.json` 不会改变主链校验。
公共查询和页面当前仍要求最终存储结构兼容，查询 Provider 的领域定制尚未实施。
