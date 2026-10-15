# SituationViews

实验性的 Event V4 地图与实体关系视图。该目录可整体移除；它只读取归档集合和派生事件
索引，不写数据库，也不参与采集、分析、评分、归档、聚合或图谱扩展。

独立运行（直接读取 `_config/config.json` 指向的真实 MongoDB）：

```bash
python -m SituationViews.standalone
```

然后访问 `http://127.0.0.1:5010/situation/`。如果后续决定并入主站，可使用
`SituationViewsService.mount_to_app()` 挂载；目前主站组合根不依赖本模块。

当前地图只使用 `event_location` 中明确给出的 ISO 两位 `country_code`，在国家中心显示
热点；点击热点会按当前筛选条件加载对应的真实情报摘要，标题链接到主站详情页。单次最多
返回最新 50 条，避免热点数据量增大后拖慢地图首屏。具体地点名称会随热点详情展示，但在
放大到区域级别后才会由独立后台任务按需解析坐标并逐步显示。解析结果缓存在
`_data/SituationViews/geocoding.sqlite`，外部坐标来自 OpenStreetMap Nominatim；解析失败的
地点不会伪造坐标，也不会影响国家级热点和其他功能。

实体关系图依据 Event Registry 的 `ArgumentRoleSpec` 将 subject 指向 object。关系型多方
事件使用无向投影，并限制参与方数量。节点当前按 `(名称, 类型, 国家)` 临时归一化，因此
属于实体提及图，不等同于完成别名消歧的知识图谱。
