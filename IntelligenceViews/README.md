# IntelligenceViews

情报多视图的纯展示集成层，提供：

- 情报列表；
- 地域视图；
- 实体视图。
- 实体时间线。

该模块不访问 MongoDB，不导入 IIS 查询引擎，也不包含地图或实体关系业务代码。它只生成
已有子页面的 URL，并通过懒加载 iframe 组织 Tab。因而列表、
`SituationViews` 和本集成页可以分别修改或移除。

IIS 挂载后的默认入口为 `/views/`。URL 只可用 `view` 选择初始 Tab：

```text
/views/?view=timeline&entity=ent_us&entity=ent_white_house&mode=lanes&orientation=vertical
```

入口默认公共可读；设置 `optional_subsystems.intelligence_views.public_access=false`
可以恢复登录保护。公共模式只同步放开地图和实体图所需的固定范围 scoped API，其他
SituationViews 查询接口不随之公开。

公开范围可单独收紧：`public_window_days` 同时限制地域、实体和时间线的数据窗口，
`public_timeline_max_entities` 限制未登录用户可带入时间线的实体数量。两项限制均由服务端
强制执行；登录用户继续使用 `optional_subsystems.situation.window_days` 和 8 个实体上限。
实体图与地域图通过同源消息把已选实体参数带到时间线，父页面会再次按当前用户上限裁剪。

情报列表直接加载原 `/intelligences` 页面，初始评分阈值为 6，进入列表后仍可使用原页面的
筛选、排序和分页能力。地域、实体图和实体时间线子页面使用 `/situation/embed/map`、
`/situation/embed/entities` 与 `/situation/embed/timeline`；三者的子系统、滚动时间窗口、
最低评分和图规模只从 `intelligence_hub_web_service.intelligence_views.situation` 配置读取，
由组合根注入并在服务端 API 强制执行，不能用 URL 覆盖。旧的 `/situation/map`、
`/situation/entities` 和 `/situation/` 发布入口不再提供。

三类视图的接口字段审计、投影分级、静态资源瘦身和分阶段性能改造方案见
[`doc/20261021_view_api_projection_and_payload_plan.md`](../doc/20261021_view_api_projection_and_payload_plan.md)。
