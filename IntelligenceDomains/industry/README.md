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
