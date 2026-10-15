# IntelligenceViews

情报三视图的纯展示集成层，提供：

- 情报列表；
- 地域视图；
- 实体视图。

该模块不访问 MongoDB，不导入 IIS 查询引擎，也不包含地图或实体关系业务代码。它只校验
展示范围、生成三个已有子页面的 URL，并通过懒加载 iframe 组织 Tab。因而列表、
`SituationViews` 和本集成页可以分别修改或移除。

IIS 挂载后的默认入口为 `/views/`。支持通过查询参数指定只读展示范围：

```text
/views/?subsystem=news
       &start=2026-10-01T00:00:00Z
       &end=2026-10-15T00:00:00Z
       &min_score=6
       &view=map
```

地域和实体子页面使用 `/situation/embed/map` 与
`/situation/embed/entities`，由集成页传入子系统、开始时间、结束时间和最低评分。
独立看板 `/situation/map` 与 `/situation/entities` 仍使用服务端固定配置，二者互不替代。
