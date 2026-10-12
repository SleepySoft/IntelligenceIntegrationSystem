# Event V4 抽取 Schema 简化：核心子集协议（Core Subset）设计

**日期：** 2026-10-13
**前置文档：** `doc/20261012_event_v4_extraction_quality_investigation.md`（质量调查报告）
**状态：** v42 已于 2026-10-12 切换为生产版本（依据 `doc/20261012_prompt_v41_v42_comparison.md`：v42 零校验失败、NAZA 案例达预期结构）；v41 保留在回滚通道
**关联代码：** `event_engine/event_engine/extraction/subset.py`、`subset_prompt.py`、`prompts_event_v4.py`、`ServiceComponent/adapters/event_v4_pipeline.py`

## 1. 问题与目标

质量调查报告（2026-10-12）显示：高分情报样本中 60% 存在重大事件结构问题，且错误几乎全部发生在首次 AI 抽取阶段。当前 LLM 面向的 `event-extraction/1.0` 完整契约要求模型一次性输出：

- 三层 ID 体系（ENT/E/Q）及跨字段引用完整性；
- Frame 三元组（其中 dynamics/topology 实际由谓词唯一决定）；
- 7 槽位 × 严格格式的时间归一化；
- 19 类实体、76 谓词目录、限定词迁移图词表、10 种事件关系；
- schema_version、primary_event_id 等机械字段。

本次改进目标：**在不降低存储表达能力的前提下，缩小 LLM 负责的语义面，把机械性、可推导的内容移交确定性代码**，从而减少出错面、提高输出确定性。

核心原则：**LLM 只做语义判断，代码做形式化**。存储契约（`event-extraction/1.0`）保持不变，归档、索引、查询等下游零改动。

## 2. 理论与实证依据

### 2.1 经典 IE 框架的"核心 / 外围"分层

主流信息抽取框架一致地把事件的**语义身份**（谓词 + 核心参与者）与**通用修饰**（时间、地点、方式）分开：

| 框架 | 核心层 | 外围/通用层 |
|---|---|---|
| ACE 2005 | trigger + participants | attributes（Time、Place 等） |
| FrameNet | core frame elements | peripheral（Time/Place/Manner）、extra-thematic |
| PropBank | ARG0/ARG1（谓词特异） | ARGM-TMP/ARGM-LOC/ARGM-MNR 等跨谓词通用修饰 |
| AMR | :ARG0–:ARG5（谓词核心） | :time、:location 等通用关系 + date-entity 子结构 |

含义：LLM 的注意力应集中在谓词选择与核心论元绑定上；外围修饰与机械字段最适合拆给确定性组件或独立子任务。

### 2.2 Schema 复杂度与输出质量的实证

- **DeepJSONEval**（arXiv:2509.25922）：随嵌套深度增加，所有 LLM 的结构化输出质量系统性下降，中等（3–4 层）到困难（5–7 层）strict score 下降 17–37 个百分点。嵌套深度与字段数是首要敌人。
- **SOB**（Structured Output Benchmark）：约束解码下 JSON 语法通过率 ≈99%，但字段值准确率只有 67–83%——**约束只保证格式，不保证语义**。语义正确性要靠缩小任务范围、校验与修复保证。
- **Instructor 工程实践**：字段命名与字段集合本身即是提示的一部分；删去可由代码推导的字段、保留推理类字段，是提升准确率的直接手段。
- **两阶段 / 分治抽取**：把单一提示拆成聚焦子任务在工程上反复被证明优于加大提示（如 btcopilot 总准确率 +12%）；流水线架构的代价是错误传播，而"LLM 核心 + 确定性后处理"恰好把传播路径切断在非 LLM 环节。
- **归一化交给规则工具**：时间归一化（HeidelTime/SUTime 系）与地理归一化（GeoNames gazetteer + 消歧启发式）是确定性工具的成熟领地；"永远不要让神经模型做日历算术"。
- **MCX 模式**：让 LLM 输出简化 schema，确定性代码桥接到严格目标格式——"即使 LLM 输出略有偏差，最终 JSON 依然鲁棒可复现"。这正是本设计采用的形态。

### 2.3 对现状的映射

调查报告 §5 的根因与上述依据一一对应：

| 调查报告根因 | 对应依据 | 对策 |
|---|---|---|
| 结构合法 ≠ 角色语义正确（§5.1） | SOB：语法通过 ≠ 值正确 | 缩小 schema，让校验算力集中在语义面 |
| 强行套用近似谓词（§5.2） | 76 行目录压迫 + null+gloss 回退存在感低 | 子集 prompt 强化"宁可 null+gloss"指示（保留） |
| 缺少跨字段一致性（§5.3） | 校验-修复循环应聚焦 | ID 引用类错误整类消除；语义检查留给后续 |
| 评分与抽取质量未分离（§5.4） | 抽取/评分分离实践 | 后续工作（见 §7） |

## 3. 字段分类：LLM 语义判断 vs 代码推导

对完整契约逐字段的分类（★ = 本次已实施移交代码）：

| 字段 | 分类 | 理由 |
|---|---|---|
| `schema_version` | ★ 代码 | 固定字面量 |
| `entities[].id` / `events[].id` / `qualifiers[].id` | ★ 代码 | 序号分配；LLM 输出 ID 无确定性且是引用错误的根源 |
| `primary_event_id` | ★ 代码 | LLM 只标 `is_primary` 布尔；代码转换为 ID 引用 |
| `core.frame`（已登记谓词） | ★ 代码 | 76 个谓词全部有唯一注册 Frame（实测 multi_frame=0），查表即得 |
| `core.frame`（开放谓词 id=null） | LLM | Registry 无规则，topology 决定兜底角色集，必须保留 |
| `agency`（19 个 agency=unknown 谓词） | LLM 提示 + 代码兜底 | 子集保留可选 `agency` 提示；Registry 已固定的谓词忽略提示 |
| 角色引用完整性 | ★ 代码 | 实体内联（按 name 合并）后引用错误整类不存在 |
| 限定词 `scope` | ★ 代码 | 一律 event 级；放弃 LLM 侧嵌套（见 §4.3） |
| 关系 `target_event_id` | ★ 代码 | LLM 输出 events 数组位置序号（从 1 开始），代码转换为 E-id |
| 时间 `normalized` 格式 | ★ 代码修复 | 宽松解析 + 截断/剥时区/日历校验；不可修复降级为 null 而非整次重试 |
| 谓词选择（含 null+gloss） | LLM | 核心语义判断 |
| 角色绑定（哪个实体填哪个角色） | LLM | 核心语义判断，调查报告的主要错误面 |
| 实体 name/type/country_code | LLM | 需要文本理解与世界知识；gazetteer 无法处理人名/机构名的国别关联 |
| 时间槽位选择与 surface/precision | LLM | 语义判断（事件发生在何时）；仅格式修复移交代码 |
| 限定词 type/value/by | LLM | 语义判断（计划状态、陈述主体等） |
| attributes | LLM | 语义抽取 |
| TIME/LOCATION/PEOPLE/ORGANIZATION 速查字段 | 代码（现状） | 归档层派生，本次不变 |
| APPENDIX、总分、UUID | 代码（现状） | 本次不变 |

## 4. 核心子集协议设计

### 4.1 LLM 输出形态（v42）

```json
{
  "events": [{
    "is_primary": true,
    "predicate": {"id": "attack", "surface": "袭击"},
    "roles": {
      "actor": [{"name": "某武装组织", "type": "organization"}],
      "target": [{"name": "乌克兰的发电厂", "type": "facility", "country_code": "UA"}]
    },
    "qualifiers": [{"type": "directive", "value": "ordered", "by": [{"name": "...", "type": "person"}]}],
    "relations": [{"predicate": "causes", "target": 2}]
  }]
}
```

与完整契约的差异：

1. **无 ID 体系**：实体内联在 roles/context/qualifier.by 中；同名实体（规范化 name：去空白 + casefold）自动合并，代码按首次出现顺序分配 ENT 编号；事件按数组顺序分配 E 编号；限定词按顺序分配 Q 编号。
2. **无 frame**（已登记谓词）：查 Registry 填充；仅开放谓词（id=null）必填 frame。可选 `agency` 提示仅对 Registry 标注 agency=unknown 的 19 个谓词生效。
3. **无主事件 ID 引用**：`is_primary` 布尔标记；零个标记时默认首事件，多个标记报错重试。
4. **关系用位置序号**：`target` 为 events 数组的 1 起始序号，越界/自指由桥接层报出明确错误。
5. **扁平事件结构**：去掉 `core` 一层嵌套（实证：嵌套深度是输出质量首要敌人）。
6. **限定词无 id/scope**：一律 event 级。
7. **时间宽松解析**：子集模型不校验 normalized 格式；桥接层修复（空白→T、剥时区、截断秒、按声明精度截断、日历校验），不可修复时降级 normalized=null（契约允许）。

### 4.2 桥接层

`event_engine.extraction.complete_core_extraction(subset, registry) -> EventExtractionResult`：

1. 修复时间表达式 → 2. 遍历事件（roles → context → qualifier.by 顺序）合并实体并分配 ID →
3. 派生/校验 Frame → 4. 编号事件与限定词、转换关系序号 →
5. 组装完整 `event-extraction/1.0` 字典 → 6. 走原有的结构 + Registry 语义校验
   （`validate_event_extraction`，含兜底角色、限定词词值、Frame 一致性检查）。

桥接失败抛出 ValueError，进入 pipeline 现有的"错误摘要回拼 prompt 重试"循环（最多 3 次），与格式错误同等处理。

### 4.3 表达能力分析（不降低表达能力 = 存储契约不变）

存储与下游消费的仍是完整 `event-extraction/1.0`，归档、事件索引、查询、前端零改动。LLM 接口侧放弃的两项能力：

| 放弃项 | 影响评估 | 缓解 |
|---|---|---|
| 限定词 scope 嵌套（限定词修饰限定词） | 调查样本中未出现实际使用；属罕见边缘 | 存储契约仍支持，未来确有需要可在子集中恢复 |
| 同名不同指实体的区分（如两位"张伟"） | 合并键是规范化 name | prompt 要求实体名保留消歧类别词（如"张伟（国防部长）"）；新闻文体中专名通常自带限定 |

### 4.4 Prompt 变化

- V42 使用 `build_core_extraction_section(registry)` 生成的子集协议章节；
- 移除：ID 分配与引用完整性规则、Frame 逐字复制规则（76 个谓词）、primary_event_id 说明、Q-id/scope 规则；
- 保留：完整谓词目录（76 行，语义必需）、实体类型表、country_code 两大段规则（调查报告证明必需）、时间/地点/属性规则、限定词目录、事件关系规则；
- **新增"角色落点"章节**（落实调查报告 P0 建议）：甲要求乙调查丙的角色分配、"调查消息源 ≠ 调查作品"、实体名保留消歧类别词、计划/拟议事件必须加限定词、机构判断必须保留陈述主体（epistemic.by）。

注：子集章节字符数与完整章节接近（结构规则减少 + 角色落点规则增加大致相抵），本设计的收益在**输出 schema 复杂度与失败类别的消除**，不在 prompt 长度。

## 5. 接入方式与灰度路径

- `prompts_event_v4.py`：
  - `EVENT_ANALYSIS_PROMPT_TABLE = {42: ...}` —— 生产抽样表，v42 已于 2026-10-12 切换为生产版本；
  - `EVENT_ANALYSIS_EXTRA_PROMPTS = {41: ...}` —— v41 回滚/对照通道，仅供显式选择；
  - `PROMPT_EXTRACTION_MODES = {41: "full", 42: "core_subset"}` —— 版本 → 协议模式映射。
- `EventV4PipelinePorts`：新增 `extra_prompt_versions`、`extraction_modes` 构造参数（默认取上述常量）；生产抽样只用 `prompt_table`，显式版本选择（调试路由）可用并集；校验按版本模式分派，core_subset 先桥接再走统一校验。
- 调试后台（`ManualDebugAnalysisService`）的 prompt_catalog 自动暴露 v42，可直接用于对照实验；归档 APPENDIX 的 `__PROMPT_VERSION__` 区分 41/42 数据，满足调查报告 P0 的可追踪性要求。

**建议的评估流程：**

1. 用调试路由对同一批正文分别跑 v41/v42，对照结构错误率（重点：调查报告的重大问题类别——角色错位、阶段缺失、地点丢失）；
2. 统计 v42 的校验一次通过率与平均重试次数（`_data/analysis_failures/` 的 validation 类样本可按 prompt_version 分组对比）；
3. 评估达标后把 42 移入生产抽样表（可先 `{41: w1, 42: w2}` 灰度，再全量切换）；
4. 切换后 v41 保留在 extra 表中用于回溯对照。

## 6. 本次改动清单

| 文件 | 改动 |
|---|---|
| `event_engine/event_engine/extraction/subset.py` | 新增：核心子集模型（SubsetEntity/SubsetEvent/CoreExtractionSubset 等）、时间修复 `normalize_time_text`、桥接 `complete_core_extraction` |
| `event_engine/event_engine/extraction/subset_prompt.py` | 新增：`build_core_extraction_section` / `build_core_extraction_prompt`（含 surface 逐字要求） |
| `event_engine/event_engine/extraction/__init__.py` | 导出新 API |
| `prompts_event_v4.py` | 新增 V42、`EVENT_ANALYSIS_EXTRA_PROMPTS`、`PROMPT_EXTRACTION_MODES`、模式常量 |
| `ServiceComponent/adapters/event_v4_pipeline.py` | 新增 extra_prompt_versions/extraction_modes 参数、按版本的校验分派、调试 prompt 表暴露额外版本；新增 shadow_versions/shadow_recorder/quality_enabled 参数、影子链路执行、归档质量统计 |
| `ServiceComponent/extraction_quality.py` | 新增：未登记角色名、角色-实体类型兼容性、surface 接地率三类确定性检查（仅统计） |
| `ServiceComponent/analysis_shadow.py` | 新增：`ShadowAnalysisRecorder` 影子分析落盘（JSON 文件，超量淘汰） |
| `ServiceComponent/intelligence_contracts.py` | 新增 APPENDIX 键 `__EXTRACTION_QUALITY__` |
| `IntelligenceHubStartup.py` | 装配影子链路（`intelligence_hub.shadow_analysis.*` 配置） |
| `event_engine/tests/test_extraction_subset.py` | 新增 12 个桥接单元测试 |
| `Test/test_extraction_quality.py` | 新增 5 个质量统计单元测试 |
| `Test/test_event_v4_pipeline_adapter.py` | 新增 7 个 pipeline 集成测试（桥接归档、重试反馈、调试目录、影子链路、质量统计） |

生产当前使用 v42；影子链路默认关闭（versions 为空），评估新版本时把其加入影子表即可与生产版本并行对比。

## 6.1 并行链路（保守灰度）设计

两条同构输出的分析链路并存：

- **主链（v41 直出）**：生产抽样表不变，归档数据全部来自 v41；
- **影子链（v42 + 桥接后处理）**：主链分析成功后，对同一输入并行执行 v42 分析（走同一重试与校验通道，经桥接补齐为完整契约），结果由 `ShadowAnalysisRecorder` 写入 `_data/analysis_shadow/`（每样本一个 JSON，含影子分析结果、质量指标、主/影子版本号、模型信息），**不进入归档、事件索引、向量库等任何下游**。

特性：

- 影子链路的任何失败（AI 调用、校验、落盘）都被隔离，仅记录日志与 `shadow_statistics` 计数，绝不影响主链；
- 影子分析在分析 worker 内同步执行，自然受 `ai_analysis_thread` 与客户端调度容量节流，不需要额外线程模型；
- 影子记录与归档 APPENDIX 中的 `__EXTRACTION_QUALITY__` 使用同一检查器，两条链路的指标口径一致，可直接按版本对比；
- 配置开关：`intelligence_hub.shadow_analysis.versions`（默认空=关闭；评佽新版本时填入其版本号即可与生产版本并行对比）。2026-10-12 v42 已按此路径完成评估并切换为生产版本。

## 6.2 质量统计（仅参考，不拦截）

`compute_extraction_quality()` 对通过校验的抽取结果做三类确定性检查：

1. **未登记角色名**：已登记谓词使用了 Registry 未声明的角色（形式错误，未来可升级为 reask）；
2. **角色-实体类型冲突**：按角色的 SemanticRoleGroup 做类型兼容性检查（AGENT/AUTHORITY 组只接受 person/organization/geopolitical_entity/position；LOCATION 组只接受 location/geopolitical_entity/facility），可机器拦截“作品当调查者”类错误；
3. **surface 接地率**：谓词/时间 surface 规范化后是否为原文子串。基线实测（15 条高分样本）：谓词 surface 命中率仅 9%，跨语言翻译是主因；v42 prompt 已加入“surface 必须逐字摘自正文、保持原文语言”要求，该指标用于跟踪合规率，暂不拦截。

指标写入归档 APPENDIX `__EXTRACTION_QUALITY__` 与影子记录，供按 prompt 版本/模型聚合，为后续校验收紧与枚举收窄提供决策数据。

## 7. 未实施项与后续建议

### 7.0 分析链路抽象重构（v43+ 的前置，已立项待排期）

现状：prompt 文本、协议模式、校验分派分散在三张表（`EVENT_ANALYSIS_PROMPT_TABLE` /
`EVENT_ANALYSIS_EXTRA_PROMPTS` / `PROMPT_EXTRACTION_MODES`）加 pipeline 内的硬编码分派
（`_validate_analysis` 的 if-else）。新增一个协议需要多点同步改动，且未登记版本静默
回落到 `full` 模式。

目标：**任意 prompt + 对应的前处理、后处理、校验组合都即插即用**，新增链路不改
pipeline 代码。方向：

- 引入 AnalysisChain 一等抽象：每条链 = {版本, prompt 文本, 输入前处理钩子（如注入发布时间锚点、相似消息裁剪）, 结果适配器（raw → 规范契约，complete_core_extraction 是第一个实例）, 校验器序列, lint 规则集}，集中注册；
- 校验分级显式化：block（拒绝）/ reask（定向重试）/ warn（仅统计进 `__EXTRACTION_QUALITY__`），由链路声明而非 pipeline 硬编码；
- 未登记版本的行为显式化（拒绝优先于静默回落），保持 `__PROMPT_VERSION__` 版本纪律；
- pipeline 只负责调度：选链 → 执行链 → 重试循环 → 交给归档，不再感知协议细节。

### 后续候选

按收益/成本排序（本次刻意未做，避免一次改动过大）：

1. **抽取质量与价值评分分离**（调查报告 §5.4）：增加独立的 extraction confidence 指标（如校验重试次数、桥接修复次数的归档记录），使高分低质样本可被识别。低成本，建议优先。
2. **Prompt 内容哈希入 APPENDIX**（调查报告 P0-2）：`__PROMPT_VERSION__` 只能区分整数版本，同一版本内容迭代无法追踪。
3. **constrained decoding**：`event_extraction_json_schema()` 已能生成封闭枚举 schema；对支持 structured outputs 的模型（OpenAI 兼容、Gemini）可用 JSON schema 约束解码消除格式类重试。注意实证显示约束不提升语义准确率，且可能损害推理类任务，建议只对 v42 子集 schema 试用。
4. **country_code 的 gazetteer 校验**：对 facility/location 类实体，可用内置国家/地名表做交叉核对（不替换 LLM 判断，只标记可疑值进入修复循环）。
5. **分析后语义质量检查**（调查报告 P1）：predicate-surface 冲突、directive=ordered 缺 by、标题有计划词但无限定词等启发式检查，可作为归档前的轻量 gate。子集协议已消除其中 ID/引用类别，剩余均为语义类。
6. **受影响历史数据重跑**（调查报告 P2）：v42 评估达标后，用调试路由或批量工具对 7 条重大问题记录重分析，保留旧版本摘要以便对比。
