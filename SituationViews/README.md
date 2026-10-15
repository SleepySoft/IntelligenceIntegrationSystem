# 态势洞察（SituationViews）

Event V4 的只读地缘态势与实体关系看板。该目录可整体移除；它只读取归档集合和派生事件
索引，不写数据库，也不参与采集、分析、评分、归档、聚合或图谱扩展。

IIS 内提供两个独立展示页面：

- `/situation/map`：地缘态势；
- `/situation/entities`：实体关系。

两个页面的子系统、滚动时间窗口、最低评分和图规模由服务端配置固定，页面不提供修改入口；
事件类别和实体类型仍可通过右上角可折叠悬浮图例进行筛选。根路径 `/situation/` 保持兼容，
默认展示地缘态势。

独立运行（直接读取 `_config/config.json` 指向的真实 MongoDB）：

```bash
python -m SituationViews.standalone
```

然后访问 `http://127.0.0.1:5010/situation/`。

IIS 主站会把同一套只读视图挂载到 `/situation/`，复用主站的登录保护和
`SubsystemRegistry`，但不进入 Hub 事件链。可以在 `_config/config.json` 中单独关闭整个
模块或地名解析：

```json
{
  "intelligence_hub_web_service": {
    "situation_views": {
      "enabled": true,
      "dashboard": {
        "subsystem": "news",
        "window_days": 7,
        "min_score": 6.0,
        "min_support": 2,
        "max_nodes": 80,
        "keep_high_score_singletons": true
      },
      "geocoding": {
        "enabled": true,
        "cache_file": "SituationViews/geocoding.sqlite"
      }
    }
  }
}
```

没有该配置段时默认启用，以兼容已有部署。模块初始化失败不会阻断 IIS 主服务；若仅地名
解析初始化失败，国家级热点和实体关系图仍可使用。`SituationViewsService.mount_to_app()`
仍可供独立程序或其他 Flask 应用直接挂载。

情报列表、地缘态势和实体关系的三个视图 Tab 由 IIS Core 之外的 `IntelligenceViews`
纯展示模块提供。集成页只组合已有子页面；地图或实体图逻辑不进入
`IntelligenceHubWebService`，三个视图也不互相直接依赖。

当前地图只使用 `event_location` 中明确给出的 ISO 两位 `country_code`，在国家中心显示
热点；点击热点会按当前筛选条件加载对应的真实情报摘要，标题链接到主站详情页。单次最多
返回最新 50 条，避免热点数据量增大后拖慢地图首屏。具体地点名称会随热点详情展示，但在
放大到区域级别后才会由独立后台任务按需解析坐标并逐步显示。解析结果缓存在
`_data/SituationViews/geocoding.sqlite`，外部坐标来自 OpenStreetMap Nominatim；解析失败的
地点不会伪造坐标，也不会影响国家级热点和其他功能。

实体关系图依据 Event Registry 的 `ArgumentRoleSpec` 将 subject 指向 object。关系型多方
事件使用无向投影，并限制参与方数量。节点当前按 `(名称, 类型, 国家)` 临时归一化，因此
属于实体提及图；对于名称和类型一致、且只有一个明确国家候选的实体，投影层会将缺失国家
代码的提及并入该国家节点。该规则不回写数据，也不处理别名，不等同于完成实体消歧的知识图谱。
边上显示 Registry 的规范中文谓词标签；原始多语言 label 按频次聚合后保留在悬浮提示和边详情中。
点击节点会在图下方列出该投影实体参与的真实情报，并可按施事、受事或无向参与角色过滤；
点击边则按来源实体、目标实体、规范谓词和方向精确列出支撑关系的情报。最大节点数等图规模
参数只裁剪可视化，不会再次裁剪已经选中关系的证据列表。
