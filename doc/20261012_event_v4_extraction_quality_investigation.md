# Event V4 高分情报事件抽取质量调查报告

**调查时间：** 2026-10-12 16:23:36（Asia/Shanghai）  
**调查对象：** MongoDB `IntelligenceIntegrationSystem` 数据库中的 Event V4 正式归档  
**重点记录：** `8150b416-e90e-4269-bf22-1eee538721ab`  
**报告性质：** 数据质量调查；不对新闻事实本身进行独立事实核查

## 1. 调查结论

本次调查按 `APPENDIX.__TOTAL_SCORE__ > 6` 查询正式归档，并按
`APPENDIX.__TIME_ARCHIVED__` 倒序抽取最新 10 条记录。查询时，新闻主集合共有
36 条满足条件；财经和行业集合均为 0 条。10 条样本全部由 Prompt 41 产生，其中
7 条使用 `gemini-3.1-flash-lite`，3 条使用 `gemini-3.5-flash-lite`。[^D1][^D2][^D3][^D4][^D5][^D6][^D7][^D8][^D9][^D10]

调查结论如下：

1. **高情报分值不等于事件抽取可靠。** `total_score` 衡量情报价值，不是结构化抽取置信度；本次样本中仅 1 条事件结构基本准确，3 条存在中等问题，6 条存在会影响检索、聚合或阅读理解的重大问题。
2. **主要缺陷发生在首次 AI 抽取阶段，而非归档转换或前端显示阶段。** MongoDB 中保存的 `EVENT_EXTRACTION` 已经包含错误或缺失；归档和前端只是按记录呈现。
3. **最突出的问题是谓词与角色没有贴合原文。** 常见表现包括把“下令调查者”当作“调查执行者”、把作品当作调查对象、把导弹试射标为袭击、把提交审批和监管审查合并为同一事件。
4. **时间阶段和信息来源限定不足。** 计划中的抗议被写成已经发生；由特定机构作出的“报复性武力展示”判断没有保留陈述主体。
5. **实体类别和实体名称仍不稳定。** 企业被标为设施，作品只保留裸专名，导致人类阅读和实体聚合都可能产生误解。
6. **地点关系确实在旧结果中丢失。** “俄罗斯袭击乌克兰发电厂”记录虽然声明了“乌克兰”实体，但目标“发电厂”的 `country_code` 和事件 `context` 都为空，因此事件结构无法表达“目标位于乌克兰”。[^D9]
7. **Prompt 追踪存在版本风险。** 当前代码已经修改事件抽取章节，但 `prompts_event_v4.py` 仍将组合后的 Prompt 标记为版本 41。若不升版，修改前后的新旧结果都会记录为 Prompt 41，后续无法准确比较修复效果。

## 2. 数据范围与方法

### 2.1 查询条件

```javascript
db.intelligence_v4_archived.find(
  {"APPENDIX.__TOTAL_SCORE__": {$gt: 6}}
).sort(
  {"APPENDIX.__TIME_ARCHIVED__": -1}
).limit(10)
```

同时对以下集合执行了相同计数查询：

- `intelligence_v4_archived`：36 条；
- `finance_intelligence_v4_archived`：0 条；
- `industry_intelligence_v4_archived`：0 条。

重点记录 `8150b416-e90e-4269-bf22-1eee538721ab` 不属于最新 10 条，但因用户指出明确疑点，作为第 11 个案例单独调查。[^D11]

### 2.2 评估维度

每条记录对照 MongoDB 中的原始标题、原始正文、分析标题、摘要和
`EVENT_EXTRACTION`，检查以下内容：

- 主事件选择是否贴合报道中心；
- predicate 是否与 `surface` 及原文动作一致；
- roles 中的行动方、执行方、目标和客体是否正确；
- 事件时间、计划状态、信息来源和限定词是否保留；
- 实体名称、实体类型和国家/地点关系是否准确；
- 多事件关系的方向是否合理。

严重度定义：

- **基本准确：** 可以直接用于展示和主要检索，仅有非关键细节缺失；
- **中等问题：** 主干可用，但实体类别、地点或重要细节缺失会降低查询质量；
- **重大问题：** 主谓宾、事件阶段、谓词或事件关系错误，可能产生相反或明显误导的理解。

## 3. 最新 10 条高分记录评估

| 序号 | UUID / 分值 | 评估 | 主要证据与判断 |
|---|---|---|---|
| 1 | `bb8922f0-86f9-43d3-a7c4-0992059d2bcc` / 7.1 | 重大问题 | 分析摘要说阿尤索“试图”把抗议活动定性为恐怖主义，但结构把 `target` 写成“太阳门广场抗议者”，把活动与参与者混为一体；未表达尝试或未完成状态，西班牙国别也未保留。[^D1] |
| 2 | `4e4696a0-8101-4516-8e9f-758d614ca6fd` / 8.4 | 重大问题 | 原始标题明确是抗议前瞻，分析标题却写成“印度发生抗议”；结构给出 `start_time=2026-10-10`，但没有 planned/expected 限定，容易把计划事件当作已发生事件。[^D2] |
| 3 | `8ef4d42a-646e-4e77-8840-a1696ec1cfa3` / 6.8 | 中等问题 | 暗访检查的主体、时间范围基本正确，但“厦门船舶重工股份有限公司”和“漳州贝纳服饰有限公司”被标为 `facility`，实际名称指向企业组织；隐患发现也没有形成独立事件。[^D3] |
| 4 | `745b0e81-0b39-4fa4-b08e-2eea36d257ae` / 6.6 | 重大问题 | 原始报道的当前进展包括曼城已被裁定违反 114 项规则及比赛背景，主事件仍被抽为“英超调查曼城”；分析摘要又写成“被指控”，事件阶段出现回退。[^D4] |
| 5 | `bf690bcb-a351-4595-87c7-93676aa562e5` / 6.7 | 重大问题 | 向海域发射导弹被映射成 `attack`，但 roles 没有 `target`；韩国军方关于“武力展示”的判断也未用 epistemic 限定保留陈述主体。[^D5] |
| 6 | `aeaee497-1ddf-4815-a71f-ffb97bbffc6b` / 6.8 | 中等问题 | “召开专家座谈会”被映射为 `regulate`，能表达监管主题但动作不精确；参与者、北京地点和意见建议均未进入事件结构。[^D6] |
| 7 | `c458db6e-e6a0-423c-913a-4a17e7c1f71e` / 6.1 | 重大问题 | `predicate=inspect` 的 `surface` 却是“提交审批”，将企业提交项目和环保局审查合并；未来生产事件与审批事件之间的 `condition_for` 方向也与“获批后方可建设生产”的自然关系相反。[^D7] |
| 8 | `40620156-5ad5-4b50-a2a3-ce3a4d29ae42` / 6.3 | 基本准确 | 国务院、两项政策、批复动作和 `authorization=approved` 相互一致；主要缺失是明确时间，但不改变核心事件含义。[^D8] |
| 9 | `b2220b56-8ed4-4dc3-97c3-acb5e6e8d3cb` / 8.5 | 重大问题 | 标题和摘要明确写“乌克兰发电厂”，但目标实体“发电厂”的 `country_code=null`，事件 `context=null`；存在“乌克兰”实体却没有任何角色或地点引用，结构无法说明发电厂位于乌克兰。[^D9] |
| 10 | `a97e23b0-0439-4f25-8a60-bfaf18685393` / 6.2 | 中等问题 | 枪手—枪杀—受害者的主干正确，但实体仅为泛称，宾夕法尼亚州/Erie 地点、人数和枪手自杀等关键结构信息均未进入事件。[^D10] |

### 3.1 样本统计

| 指标 | 数量 | 占比 |
|---|---:|---:|
| 基本准确 | 1 | 10% |
| 中等问题 | 3 | 30% |
| 重大问题 | 6 | 60% |
| 存在中等或重大问题 | 9 | 90% |
| 缺少事件地点 `context` | 10 | 100% |

“缺少事件地点”不表示每篇报道都必须有地点；但至少 D3、D6、D9、D10 的原始内容包含明确且有检索价值的地点，结构仍未保存。

## 4. NAZA 重点案例

### 4.1 当前记录

记录 `8150b416-e90e-4269-bf22-1eee538721ab` 的原始标题是
“Netanyahu demands probe into sources for award-winning Gaza film NAZA”。分析标题和摘要也正确指出调查对象是纪录片中的匿名消息源。[^D11]

但是 `EVENT_EXTRACTION` 保存为：

```json
{
  "entities": [
    {"id": "ENT1", "name": "本雅明·内塔尼亚胡", "type": "person", "country_code": "IL"},
    {"id": "ENT2", "name": "NAZA", "type": "product", "country_code": null},
    {"id": "ENT3", "name": "以色列国防军", "type": "organization", "country_code": "IL"}
  ],
  "primary_event_id": "E1",
  "events": [{
    "id": "E1",
    "core": {
      "predicate": {"id": "investigate", "surface": "调查"},
      "roles": {
        "investigator": ["ENT1"],
        "object": ["ENT2"]
      }
    },
    "qualifiers": [{"type": "directive", "value": "ordered", "by": null}]
  }]
}
```

### 4.2 判断

前端显示“客体：NAZA”忠实反映了数据库内容，**不是前端省略了“纪录片”**。数据库中的首次抽取本身存在三项重大错误：

1. **客体错误：** 原文要求调查的是匿名消息源、参与者或军事举报人，不是纪录片本身；
2. **执行角色错误：** 内塔尼亚胡是命令发出者，不是被要求开展调查的执行者；
3. **实体命名不足：** 即使作品需要作为实体保留，也应写作“纪录片《NAZA》”，而不是没有类别词的裸专名“NAZA”。

### 4.3 建议结构

在现有 Event Engine 能力范围内，可使用 `investigate` 事件并通过 directive 限定表达命令关系：

```json
{
  "entities": [
    {"id": "ENT1", "name": "本雅明·内塔尼亚胡", "type": "person", "country_code": "IL"},
    {"id": "ENT2", "name": "以军总参谋长", "type": "position", "country_code": "IL"},
    {"id": "ENT3", "name": "辛贝特负责人", "type": "position", "country_code": "IL"},
    {"id": "ENT4", "name": "以色列总检察长", "type": "position", "country_code": "IL"},
    {"id": "ENT5", "name": "纪录片《NAZA》的匿名消息源和参与者", "type": "person", "country_code": "IL"},
    {"id": "ENT6", "name": "纪录片《NAZA》", "type": "product", "country_code": null}
  ],
  "events": [{
    "id": "E1",
    "core": {
      "predicate": {"id": "investigate", "surface": "发起全面调查"},
      "roles": {
        "investigator": ["ENT2", "ENT3", "ENT4"],
        "object": ["ENT5"]
      }
    },
    "qualifiers": [{
      "id": "Q1",
      "type": "directive",
      "value": "ordered",
      "by": ["ENT1"],
      "scope": "event"
    }]
  }]
}
```

其中 ENT6 用于保留作品身份或供其他事件引用，不应充当本次调查的 `object`。

## 5. 根因分析

### 5.1 Prompt 更强调结构合法性，弱化语义落点

现有约束可以保证 ID、Frame、角色名和引用通过 Schema 校验，但“字段合法”并不代表“角色语义正确”。NAZA 记录满足 `investigator/object` 的结构要求，却把两个角色都填错了。

### 5.2 Registry 谓词粒度不足时，模型强行套用近似谓词

“召开座谈会”“提交审批”“导弹发射”等动作没有完全贴合的谓词时，模型倾向于选择 `regulate`、`inspect`、`attack` 等近似项，而不是使用开放谓词。这会让结构看似规范，实际语义发生偏移。

### 5.3 缺少跨字段一致性检查

当前校验主要检查引用、角色集合和 Frame，尚未检查：

- `surface=提交审批` 是否真的符合 `predicate=inspect`；
- 标题中的“计划/将要”是否在 time 或 qualifier 中体现；
- 摘要中的地点是否被 event_location 或目标实体 country_code 保存；
- directive 已为 `ordered` 时是否提供命令发出者 `by`；
- 已抽取但从未引用的实体是否代表信息遗漏。

### 5.4 价值评分与抽取质量未分离

样本中 8.4 和 8.5 分记录仍存在重大结构问题，说明 `total_score` 不应被用于推断事件抽取可信度。系统目前缺少单独的 extraction quality/confidence 指标。

## 6. 修复建议与优先级

### P0：先保证新数据可追踪

1. 将修改后的组合 Prompt 从 41 升为 42，避免修复前后数据共用同一版本号；
2. 记录用于分析的 Prompt 内容哈希，避免将来仅靠整数版本无法识别运行时差异；
3. 对修复后的新数据建立独立抽样窗口，不与 Prompt 41 混合评估。

### P0：加强 Prompt 的角色落点规则

增加明确的反例和正例：

- “甲要求乙调查丙”中，甲是 directive.by，乙是 investigator，丙是 object；
- 作品报道中的“调查消息源”不得简化为“调查作品”；
- 实体名称必须保留消歧类别词，如“纪录片《NAZA》”“Rafael天然气项目”；
- 计划、拟议、威胁、要求等不能直接改写成已完成事实。

### P1：增加分析后语义质量检查

在归档前增加轻量检查并允许一次定向修复：

- predicate 与 surface 明显冲突；
- `directive=ordered` 但 `by` 为空；
- 新闻标题/摘要有明确地点，而事件没有任何地点引用；
- 实体已声明但从未被事件、地点或限定词引用；
- `start_time` 位于报道未来但没有 planned/expected 等状态；
- organization 名称带“公司/政府/委员会”等明显组织后缀却被标为 facility。

### P1：补齐常见谓词或允许可靠的开放谓词

优先复核以下动作是否应加入 Registry，或明确要求使用 `predicate.id=null + gloss`：

- 提交、申请、要求、命令；
- 召开、会谈、协商；
- 发射、试射；
- 裁定、认定。

### P2：重新分析受影响记录

优先重跑：

1. `8150b416-e90e-4269-bf22-1eee538721ab`（NAZA）；
2. `b2220b56-8ed4-4dc3-97c3-acb5e6e8d3cb`（乌克兰发电厂地点关系）；
3. 本报告判定为重大问题的其余 5 条最新样本。

重跑时不建议原位无痕覆盖；至少保留旧 Prompt 版本、旧结果摘要和重分析时间，便于比较修复收益。

## 7. 数据引用

以下引用均以调查时 MongoDB 快照为准。MongoDB 定位给出集合、UUID、分值、归档时间原始值和 Prompt 版本；链接指向记录的 `INFORMANT` 原始来源。

[^D1]: MongoDB `intelligence_v4_archived`, UUID `bb8922f0-86f9-43d3-a7c4-0992059d2bcc`, score `7.1`, archived `2026-10-12 05:40:31.757000`, Prompt `41`, model `gemini-3.1-flash-lite`; [原始来源](https://elpais.com/opinion/2026-10-12/manual-para-criminalizar-la-acampada.html)。

[^D2]: MongoDB `intelligence_v4_archived`, UUID `4e4696a0-8101-4516-8e9f-758d614ca6fd`, score `8.4`, archived `2026-10-12 05:34:12.227000`, Prompt `41`, model `gemini-3.5-flash-lite`; [原始来源](https://www.dw.com/en/india-s-cockroaches-vs-election-commission-what-you-need-to-know-ahead-of-cjp-s-october-10-protests/a-79608760?maca=en-rss-en-asia-5133-rdf)。

[^D3]: MongoDB `intelligence_v4_archived`, UUID `8ef4d42a-646e-4e77-8840-a1696ec1cfa3`, score `6.8`, archived `2026-10-12 05:33:37.879000`, Prompt `41`, model `gemini-3.1-flash-lite`; [原始来源](https://www.chinanews.com.cn/sh/2026/10-12/10711890.shtml)。

[^D4]: MongoDB `intelligence_v4_archived`, UUID `745b0e81-0b39-4fa4-b08e-2eea36d257ae`, score `6.6`, archived `2026-10-12 05:32:31.218000`, Prompt `41`, model `gemini-3.1-flash-lite`; [原始来源](https://www.bbc.co.uk/sport/football/articles/cmy0rd4pex7no?at_medium=RSS&at_campaign=rss)。

[^D5]: MongoDB `intelligence_v4_archived`, UUID `bf690bcb-a351-4595-87c7-93676aa562e5`, score `6.7`, archived `2026-10-12 05:32:13.162000`, Prompt `41`, model `gemini-3.1-flash-lite`; [原始来源](https://www.dw.com/en/north-korea-fires-missiles-after-seoul-s-hypersonic-test/a-79636110?maca=en-rss-en-asia-5133-rdf)。

[^D6]: MongoDB `intelligence_v4_archived`, UUID `aeaee497-1ddf-4815-a71f-ffb97bbffc6b`, score `6.8`, archived `2026-10-12 05:30:30.637000`, Prompt `41`, model `gemini-3.1-flash-lite`; [原始来源](https://www.chinanews.com.cn/cj/2026/10-12/10711896.shtml)。

[^D7]: MongoDB `intelligence_v4_archived`, UUID `c458db6e-e6a0-423c-913a-4a17e7c1f71e`, score `6.1`, archived `2026-10-12 05:28:16.195000`, Prompt `41`, model `gemini-3.1-flash-lite`; [原始来源](https://www.abc.net.au/news/2026-10-12/conventional-gas-project-could-meet-local-energy-demand/107247116)。

[^D8]: MongoDB `intelligence_v4_archived`, UUID `40620156-5ad5-4b50-a2a3-ce3a4d29ae42`, score `6.3`, archived `2026-10-12 05:27:17.017000`, Prompt `41`, model `gemini-3.1-flash-lite`; [原始来源](https://www.chinanews.com.cn/cj/2026/10-12/10711898.shtml)。

[^D9]: MongoDB `intelligence_v4_archived`, UUID `b2220b56-8ed4-4dc3-97c3-acb5e6e8d3cb`, score `8.5`, archived `2026-10-12 05:25:12.387000`, Prompt `41`, model `gemini-3.5-flash-lite`; [原始来源](https://www.bbc.co.uk/news/articles/crp3gydddnxdo?at_medium=RSS&at_campaign=rss)。

[^D10]: MongoDB `intelligence_v4_archived`, UUID `a97e23b0-0439-4f25-8a60-bfaf18685393`, score `6.2`, archived `2026-10-12 05:23:35.343000`, Prompt `41`, model `gemini-3.5-flash-lite`; [原始来源](https://www.abc.net.au/news/2026-10-12/father-mourns-daughter-killed-in-pennsylvania-shooting/107255530)。

[^D11]: MongoDB `intelligence_v4_archived`, UUID `8150b416-e90e-4269-bf22-1eee538721ab`, score `6.7`, archived `2026-10-12 05:09:24.969000`, Prompt `41`, model `gemini-3.1-flash-lite`; [原始来源](https://www.aljazeera.com/news/2026/10/12/netanyahu-demands-probe-into-sources-for-award-winning-gaza-film-naza?traffic_source=rss)。

