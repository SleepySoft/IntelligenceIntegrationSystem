# 议题槽错位与角色名误用：调查与修复（2026-10-17）

## 起因

归档 `bf37a558-1da2-4d20-875e-19431a1b96b1`（prompt v41 + gemini-3.1-flash-lite）出现
主谓-议题错位：`negotiate` 事件的 `topic` 槽填了人物"泽连斯基"，而议题"能源停火"
反而被塞进谓词 surface。人物被索引为"议题"，污染下游实体索引与实体关系图。

## 量化结论（近 3000 条归档扫描）

| 问题类型 | 规模 | 说明 |
|---|---|---|
| 真·议题错位（`topic` 槽填 person/animal） | 1/1558（0.06%） | 即起因案例，属偶发 |
| 已登记谓词误用开放兜底角色（subject/theme 等） | 47/1490（3.2%）含未登记角色 | 以 `regulate/object`、`agree/topic`、`terminate/actor`、`restrict/actor` 为主 |
| `theme`/`subject` 填 person | 多数合法 | transfer 的 theme（遣返/撤离人员）、intrinsic 的 subject 本来就允许人物 |

结论：议题错位是低频偶发；更普遍的问题是 **Registry 角色覆盖缺口**——模型使用的
自然角色名未在谓词声明中，被判为"未登记角色"。

## 修复内容

1. **检测层**（`ServiceComponent/extraction_quality.py`）：组级（SemanticRoleGroup）
   类型限制之外，新增按角色名判负名单 `_ROLE_DENIED_ENTITY_TYPES`：`topic` 槽禁止
   person/animal。仅统计、不拦截，今后此类错位可在 `__EXTRACTION_QUALITY__` 中按
   prompt 版本/模型聚合观测。测试见 `Test/test_extraction_quality.py`。

2. **Prompt 层**（`prompts_event_v4.py`）：新增 **v45**（= v44 + 议题槽与角色名
   指引：topic 只填抽象事项实体；已登记谓词禁止改用开放兜底角色）。注册为
   `sampling=False` 灰度版本，生产抽样仍为 v44；可经
   `intelligence_hub.shadow_analysis.versions` 影子链路对比后再切换。

3. **Registry 层**（`event_engine/domains/news/config.py`，news 包 1.5→1.6）：
   补齐高频自然角色——`agree`+`topic`、`regulate`+`object`、`terminate`+`actor`、
   `restrict`+`actor`。只拓宽可选角色，不影响身份/合并规则与既有数据。

4. **工具修复**（`Scripts/compare_prompt_v41_v42.py`）：补充移除
   `_ai_client_meta` 内部标识（与生产链一致），此前该脚本对所有样本恒校验失败。

## 验证

- 单元/集成测试：288 passed（含新增的 topic←person 判正负用例）。
- 真实模型对比（`Scripts/compare_prompt_v41_v42.py --versions 44,45`）：
  - 起因样本 v44/v45 均一次通过且结构正确（泽连斯基→communicate.sender，
    能源停火→agreement 实体），证实 v41 小模型偶发、现行协议已能正确处理；
  - `c85bccc8` 样本 v45 结构优于 v44（主事件选择更准），两版 `restrict/actor`
    在 Registry 补齐后不再计为未登记角色。

## 影子对比工具

- 影子记录落盘：`_data/analysis_shadow/`（每样本一个 JSON，含 source_uuid、
  主链/影子版本、analysis 全文与 extraction_quality；最多保留 1000 个）。
- 配对对比：`Scripts/compare_shadow_records.py` 按 source_uuid 把影子记录与
  Mongo 归档配对，并排输出版本/模型/质量指标，可选 `--events` 查看事件结构、
  `--uuid` 过滤单条、`--limit` 只看最近 N 对，末尾聚合“更优/持平/更差”。

## 后续建议

- 将 v45 配置进影子分析链路积累对比数据（`hub.shadow_versions: [45]`，可用
  `hub.shadow_sample_rate` 控制灰度 Token 成本，如 0.1 只跑约 10% 样本）；
  若 `__EXTRACTION_QUALITY__` 的未登记角色/角色类型冲突指标优于 v44，再把
  sampling 切到 v45。
- 其余长尾未登记角色组合（`remove/organization`、`adjudicate/object` 等）
  可按同样方式观察后逐个补齐。
