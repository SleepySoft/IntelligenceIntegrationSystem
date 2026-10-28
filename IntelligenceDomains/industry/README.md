# Industry domain

`industry` 是面向公司行动的独立情报域。它复用公共 Event V4 流程、Event Engine、
Mongo 查询和前端展示，但独立拥有：

- `prompt.py`：行业 Prompt v1/v2、Event Engine 核心子集桥接和协议注册；
- `predicates.py`：行业专用公司行动谓词定义及 Registry 注入；
- `schema.py`：行业分类、八维 RATE 等级词和结果校验；
- `scoring.py`：决策价值/故事兴趣双轨评分；
- `storage.py`：行业归档 DTO 与存储投影；
- `enrichment.py`：未来公司重要性资料库和 Arachne 异步评估的接口。

当前 v1/v2 各占一个生产抽样槽。v1 是冻结对照；v2 在相同 schema、谓词目录和评分器上，
增加公司行动、经营状态冲击、经营相关冲突、可执行产业政策四类信号的跨类型证据标尺，
避免把“官方发布”“行业代表性”或故事性同时扩散到所有价值维度。首次分配仍写入 cache，
持久化重试不会跨版本。灰度设计与首轮配对结果见
`doc/prompt_versions/reports/20261028_industry_prompt_v2_gray_design.md`。

两个版本都只根据正文评价。不得由 LLM 猜测公司重要性、市场份额、产业链关系或供应链传播。
`company_importance_bonus` 与 `arachne_network_bonus` 默认且实际均为 0；只有未来显式传入
版本化增强结果时，评分器才会计算受限后置加分。

领域暂不提供 Batch 和 Shadow 实现，因此 `DomainSubsystems` 对应端口为空。新增实现时
必须放在本目录，并由 `create_domain()` 显式注册，不能借用新闻领域实现。

## 生产数据源

当前生产采集入口集中在 `CrawlTasks/industry/`：

- Manufacturing Dive 新闻 RSS：美国制造业公司行动，排除赞助稿与新闻稿；
- 界面新闻商业频道：以中国商业报道为主的公司战略、管理层、产品和组织变化；
- 界面新闻工业频道：以中国产业视角为主的扩产、并购、基地、产能与产业链动态。
- 科创板日报：首页科技公司、半导体、AI 与硬科技公司行动，只收 `/detail/` 正文；
- 爱集微：首页最新资讯与焦点区，补充半导体、ICT、AI 硬件及全球科技公司动态。
- OFweek：半导体、电子工程和机器人三个中国高科技制造频道，只收 `ART` 正文；
- IndustryWeek：只收制造企业经营、扩张、财务、工厂与工业技术部署类文章，排除供应链栏目；
- TechCrunch Startups：补充国际科技公司融资、并购、管理层和战略变化，排除活动推广。

完整入口、验证结果和候选源状态见 `doc/IndustryIntelligenceSources.md`。一个媒体可以有多个
采集入口；对外说明数据源数量时，应同时报告“媒体数”和“采集入口数”。
