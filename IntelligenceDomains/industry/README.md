# Industry domain

`industry` 是面向公司行动的独立情报域。它复用公共 Event V4 流程、Event Engine、
Mongo 查询和前端展示，但独立拥有：

- `prompt.py`：行业 Prompt v1–v6、Event Engine 核心子集桥接和协议注册；
- `predicates.py`：行业专用公司行动谓词定义及 Registry 注入；
- `schema.py`：行业分类、八维 RATE 等级词和结果校验；
- `scoring.py`：决策价值/故事兴趣双轨评分；
- `storage.py`：行业归档 DTO 与存储投影；
- `enrichment.py`：未来公司重要性资料库和 Arachne 异步评估的接口。

当前为稳定性基线阶段：v2 是唯一正式生产版本，并以 25% 确定性抽样执行同模型、同 Prompt 的
A/A 重复分析；v1、v3–v6 保留为历史可选版本。已有 cache 的 v4 分配继续按 v4 完成，但不进入
v2 A/A。v3 保留 v2 对公司行动、经营状态冲击、经营相关冲突、可执行产业政策四类信号的
覆盖和详细 REASON，消除财报边界冲突，并用准入门槛、逐维保守升档和事件优先级取代较长的校准
说明；v4 不改变这些价值判断，仅增加 REASON 长度、角色数组和空可选对象的强制自检。v5 曾尝试
通过缩短字段预算和强化容器约束提高首过率，但首轮效果更差，已退出当前 Shadow。v6 不继承 v5，
而是从 v4 直接派生，只针对已观测到的顶层字段、attributes 键/类型和实体类型自造问题增加闭集约束。
本轮 A/A 不比较 Prompt 优劣，只测量分类、评分、实体、事件和首过率的模型固有波动。
历史版本设计与灰度记录见
`doc/prompt_versions/reports/20261028_industry_prompt_v2_gray_design.md` 和
`doc/prompt_versions/reports/20261029_industry_prompt_v3_shadow_design.md`；v3 首次运行失败及 v4 修正
记录在 `doc/prompt_versions/reports/20261029_industry_prompt_v4_validation_fix.md`；v4 转正与 v5 设计见
`doc/prompt_versions/reports/20261030_industry_prompt_v4_promotion_v5_first_pass.md`。首次运行审计、历史
cache 污染原因和灰度准入修复见
`doc/prompt_versions/reports/20261101_industry_v4_v5_shadow_audit.md`；v6 设计与灰度边界见
`doc/prompt_versions/reports/20261101_industry_prompt_v6_schema_gray.md`。

Shadow 实验必须设置稳定的 `experiment_id`。抽样由
`SHA256(experiment_id + domain_name + UUID)` 确定，重启与重试不会改变分组；
`comparison_mode=replicate` 专用于正式 Prompt 的同模型 A/A 重复实验。

两个版本都只根据正文评价。不得由 LLM 猜测公司重要性、市场份额、产业链关系或供应链传播。
`company_importance_bonus` 与 `arachne_network_bonus` 默认且实际均为 0；只有未来显式传入
版本化增强结果时，评分器才会计算受限后置加分。

领域暂不提供 Batch。Shadow 由 `subsystems/shadow.py` 独立注册，版本、采样率和比较模式读取
`_config/domains/industry.json` 的 `subsystems.shadow`；结果写入 MongoDB `analysis_shadow`
集合，不进入正式归档、事件索引或向量库。

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
