# Event V4 v49：独立构建与语义保留式精简

日期：2026-10-20。

状态：v49 已完成首轮小样本生产灰度；因已登记谓词的角色白名单/实体类型
遵循率下降，现已退出生产抽样并保留为 `sampling=False` 对照。修复版 v50 见
[`20261020_prompt_v50_role_guard_gray.md`](20261020_prompt_v50_role_guard_gray.md)。

## 目标与边界

- 保持 v48 的语义要求、模型输出结构、评级等级及后处理协议。
- 新版不读取、替换或追加任何历史 Prompt；旧版仅供对照和回滚。
- 提取目录中重复的公共角色与 Frame，合并分散的说明。
- 不引入 v45 的 topic/角色名专项增强，不删减谓词定义、评级准入提示或原因码。
- 不改 Registry、数据模型、归档、消息构造器、重试策略与存储契约。

## 实际构建路径

```text
prompts_event_v49.build_event_v49_prompt(registry)
  └─ build_event_v49_sections(registry)
      ├─ task：任务与输出总则
      ├─ non_intelligence：价值判断、低价值模板、原因码
      ├─ valuable_output：完整顶层示例、字段要求
      ├─ taxonomy：TAXONOMY_SUBCATEGORIES
      ├─ rating：评级规则 + RATE_LEVELS（完整释义和准入提示）
      ├─ extraction：Event Engine 独立编写的核心子集说明
      └─ predicates：Registry → 公共可选角色 + 完整 Frame 分组
```

`prompts_event_v4.py` 仅导入并注册 v49；该版本与 v48 绑定同一个
`core_subset_levels_reason` 协议对象。构建时传入的 Registry 也传给新目录和限定词生成器。
新版仍使用稳定 `system` + 动态 `user`；日期、元数据、正文和重试反馈的处理不变。

Event Engine 的新版规则位于 `event_engine/event_engine/extraction/compact_prompt.py`；
谓词目录格式由 `build_compact_predicate_catalog()` 生成。实体类型、时间槽、属性键、
关系谓词及开放 Frame 枚举读取现行模型；限定词值继续从 Registry 生命周期规则生成。
IIS 不复制这些枚举或维护另一套谓词规则。

## 压缩方式

当前合并 Registry 包含 79 个谓词，所有谓词共有六个可选角色：
`beneficiary,destination,instrument,location,source,target`。
原目录逐行重复这些角色；新版声明一次，每行只列剩余角色。

公共角色按所有谓词的“允许角色减去必填角色”的交集动态计算，不硬编码这六项；
完整角色集合 = 公共角色 ∪ 每行角色。若将来某个角色变成某谓词的必填角色，
它不会被提取到公共可选角色中。空 Registry、没有公共角色与单谓词 Registry 均有覆盖。

完整 Frame 按三元组分组。当前共 16 组。所有 `allowed_frames` 及其顺序均保留，
不把多种合法 Frame 合并为一个，也不隐藏 `agency=unknown` 的可选判断能力。
谓词 ID、中文名、完整定义、允许角色、必填角色均未删除。

## 规则对照

| v48 规则 | v49 位置与处理 |
|---|---|
| 中文、单 JSON、正文事实来源、历史仅作增量参考、可选字段省略 | task，集中说明 |
| 禁止额外包装层、系统元数据与派生字段 | task，完整保留禁止字段 |
| 低价值 JSON 模板重复两次 | non_intelligence，仅保留一次 |
| 14 个低价值原因及前五项质量缺陷优先 | non_intelligence，原因码引用共享枚举 |
| 有价值顶层字段与独立事件示例 | valuable_output，将事件示例直接嵌入顶层对象 |
| 标题/摘要/正文/影响/原因/提示长度、分类与子分类限制 | valuable_output，逐字段列出 |
| 六维等级词、顺序、释义、所有准入提示、重复及回顾评级上限 | rating，完整保留 |
| 1–3 个事件、唯一主事件、内联实体、服务端分配局部 ID | extraction / 事件结构 |
| 已登记谓词 Frame 补齐、unknown agency、开放谓词与 gloss | extraction / 事件结构与角色 |
| 指令方/执行方/客体分离、消息源对象、名称消歧 | extraction / 实体与角色，保留原案例 |
| 实体国别、事件地点、禁止从攻击方推断目标国别 | extraction / 时间与地点，合并说明但保留两类信息 |
| 最小时间对象、七种时间槽、禁止 precision/approximate | extraction / 时间与地点 |
| 八种属性键、四种对象形状、比例范围 | extraction / 数值属性，保留示例 |
| by 规则、事件级作用域、阶段、计划、陈述主体、未证实、否定 | extraction / 限定词与信息状态，合并 v44 补丁 |
| 十种关系、1 起始目标序号、方向、自指限制与避免重复拆事件 | extraction / 事件关系及事件选择 |
| 谓词和角色目录唯一事实源 | predicates，从 Registry 完整生成 |

表述澄清而非新增功能：

- “评级以 `primary_event_id` 为锚点”改为“以 `is_primary=true` 的主事件为对象”；
  前者属于桥接后的存储结构，后者是现行 LLM 输出字段，指向同一事件。
- 删除“沿用 V2”“直接符合 AnalysisResultV4”等历史背景，直接给出现行输出格式。
- “不输出任何 ID”明确为不输出实体/事件/限定词的局部 ID，保留合法的 `predicate.id`。
- 保留省略空对象规则，并明确既有低价值 `RATE={}` 是规定的例外。
- 开放 Frame 的字段枚举显式读取现有契约；公共目录角色不推广到开放谓词。

## 长度

在本次 Registry 下，固定 Prompt（不含正文、动态元数据与重试反馈）：

| 版本 | 字符数 | UTF-8 字节数 |
|---|---:|---:|
| v48 | 21,822 | 32,250 |
| v49 | 14,004 | 23,538 |

字符减少 7,818（35.8%）。这是字符统计，不是模型 Token 或费用测量。
段落分组使行数从 268 增至 280，但去除了大量行内重复信息。

v49 章节字符数（去除章节首尾空白，不计拼接分隔符）：

| 章节 | 字符数 |
|---|---:|
| task | 327 |
| non_intelligence | 962 |
| valuable_output | 1,018 |
| taxonomy | 152 |
| rating | 1,486 |
| extraction | 3,688 |
| predicates | 6,358 |

## 验证与使用

已完成：

- 将精简目录重新解析，对比 Registry 的全部谓词、名称、定义、角色及 Frame；
  覆盖独立领域包、组合包、必填角色、多个 Frame 顺序和空/单谓词边界。
- 对比 v48/v49 对同一输出的桥接结果：普通示例、含时间/国别/属性/限定词/关系/
  agency/开放谓词的复杂输出、全部低价值原因；错误评级和缺失原因仍被拒绝。
- 验证 v49 不消费旧版文本或调用旧版 Prompt 构造器。
- 验证稳定 system、动态 user、重试追加布局和人工调试选择 v49 的无持久化路径。
- 与改动前版本比较：v41–v48 文本逐字不变。

回归命令（本次 125 passed）：

```bash
python -m pytest -q \
  Test/test_prompt_v49.py Test/test_event_v4_schema.py \
  Test/test_prompt_protocols.py Test/test_rate_levels.py \
  Test/test_event_v4_pipeline_adapter.py Test/test_ai_client_benchmark.py \
  Test/test_analysis_batch.py Test/test_manual_debug_analysis.py \
  event_engine/tests/test_compact_prompt.py \
  event_engine/tests/test_extraction.py event_engine/tests/test_extraction_subset.py
```

人工调试页可选择 v49；该页按现有逻辑默认选最高可选版本。离线对比工具
`Scripts/compare_prompt_v41_v42.py` 也能显式选择 `--versions 48,49`。本次没有执行真实 AI 请求。

灰度期按模型与 Prompt 版本分组，核查首次通过率、事实接地、主事件和角色、
计划/否认等状态、时间国别、评级分布及低价值原因；特别关注提取到公共段的关键角色
是否仍被正确填写。`python Scripts/analysis_model_report.py` 可查看模型×Prompt 版本的
成功率、首过率、敏感率、错误分布和延迟；样本足够后再决定全量 v49 或回退为 v48。
