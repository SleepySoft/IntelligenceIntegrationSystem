# Industry domain

`industry` 是面向公司行动的独立情报域。它复用公共 Event V4 流程、Event Engine、
Mongo 查询和前端展示，但独立拥有：

- `prompt.py`：行业 Prompt v1、Event Engine 核心子集桥接和协议注册；
- `predicates.py`：行业专用公司行动谓词定义及 Registry 注入；
- `schema.py`：行业分类、八维 RATE 等级词和结果校验；
- `scoring.py`：决策价值/故事兴趣双轨评分；
- `storage.py`：行业归档 DTO 与存储投影；
- `enrichment.py`：未来公司重要性资料库和 Arachne 异步评估的接口。

第一版只根据正文评价公司行动。不得由 LLM 猜测公司重要性、市场份额、产业链关系或
供应链传播。`company_importance_bonus` 与 `arachne_network_bonus` 默认且实际均为 0；
只有未来显式传入版本化增强结果时，评分器才会计算受限后置加分。

领域暂不提供 Batch 和 Shadow 实现，因此 `DomainSubsystems` 对应端口为空。新增实现时
必须放在本目录，并由 `create_domain()` 显式注册，不能借用新闻领域实现。

## 生产数据源

当前生产采集入口集中在 `CrawlTasks/industry/`：

- Manufacturing Dive 新闻 RSS：美国制造业公司行动，排除赞助稿与新闻稿；
- 界面新闻商业频道：以中国商业报道为主的公司战略、管理层、产品和组织变化；
- 界面新闻工业频道：以中国产业视角为主的扩产、并购、基地、产能与产业链动态。
- 科创板日报：首页科技公司、半导体、AI 与硬科技公司行动，只收 `/detail/` 正文；
- 爱集微：首页最新资讯与焦点区，补充半导体、ICT、AI 硬件及全球科技公司动态。

完整入口、验证结果和候选源状态见 `doc/IndustryIntelligenceSources.md`。一个媒体可以有多个
采集入口；对外说明数据源数量时，应同时报告“媒体数”和“采集入口数”。
