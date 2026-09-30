# EventIR 与通用内核：实现决策记录

更新：2026-09-30。本文记录本次已实现的边界、判定规则和兼容性变化；
`extend_design.md`、`leveling_design.md` 中更宽的协议草案不代表全部已经实现。

## 1. 程序边界

```text
event_engine/
├── schema/             公共数据与契约，不加载执行或存储实现
│   ├── models.py       EventIR、观察、CanonicalEvent、匹配结果、状态投影
│   ├── specs.py        PredicateSpec、IdentitySpec、LifecycleSpec、DomainPack
│   ├── queries.py      EventQuery、EventPage
│   └── ports.py        EventRepository、CanonicalEventRepository 协议
├── core/
│   ├── registry.py     配置注册、冻结与语义校验
│   ├── analyzer.py     通用角色分析、时间线、状态投影入口
│   ├── canonicalizer.py 身份比较与 CanonicalEvent 重算
│   ├── state.py        带来源和有效时间的生命周期投影
│   ├── temporal.py     时间精度与区间计算
│   └── engine.py       查询、登记、匹配与绑定的用例编排
├── configs/            news、industry、financial 三类只读规则包
├── extensions/news.py 新闻专用分析（如战争区域）
├── query/              内存、MongoDB 与序列化适配器
├── integration/        文件接入及兼容服务入口
└── analysis/           旧分析服务的兼容入口
```

依赖方向是 `core → schema`；配置包的规则声明、领域扩展、存储适配和接入代码
共享 schema 公共协议。配置组合加载由 Registry 完成。
核心不导入三个配置包，不根据 `domain_id` 分支，也不依赖 MongoDB 或 IIS。
行业影响、金融收益等业务推导不进入通用内核。

本次组织调整将数据与契约统一到 `schema`，不是修改事件语义或存储格式。
`DomainPack` 也是声明性数据，移出 Registry；`LifecycleSpec` 只保留迁移图，
可达性算法移到 `core.state.lifecycle_allows`。`EventIR` 名称保留，表示纯事件语义，
但其所属 schema 还包含观察外壳、派生结果和接口契约。
已删除的 `domain/` 不恢复兼容包装，原 `ir/` 和 `core.models/specs/queries/ports`
导入路径也不保留；所有仓库内调用已更新，外部调用需改为 `event_engine.schema`。

配置不只是外部输入约束，也提供内部推理所需的语义：角色投影、身份维度、
时间窗口、Frame 和状态迁移。内核知道这些规则，但不需要知道它们属于哪个领域。
这是“领域知识作为数据，通用算法消费协议”，不是“无语义配置的通用推理”。

三个包按词汇维护职责划分，并非互斥业务分类；新闻涉及金融时组合加载金融包。
当前包版本为 `1.1`。`DomainPack` 实际是包含 `domain_id`、`version`、`specs` 的数据类，
不是扩展草案中的实体类型注册服务。

`EventIR` 不含来源与存储身份；`EventRecord` 补充观察 UUID、情报 UUID、局部 ID、
到达时间和元数据。目前保留平铺构造参数与旧存储格式，通过 `.ir` 提取语义，
通过 `EventRecord.from_ir(...)` 添加观察外壳，没有强制改成嵌套存储。

## 2. 修复一：Frame 从描述变成可执行约束

原问题不是缺少 Frame 概念，而是定义与执行没有闭环。现在内置谓词均声明 Frame，
Registry 在接入校验，匹配器也校验观察和候选，避免绕过接入后带入不兼容 Frame。

`PredicateSpec.frame` 表示默认结构；`allowed_frames` 非空时声明允许的结构变体。
`dynamics`、`topology` 必须符合定义；`agency=unknown` 与已知 agency 兼容，不当成明确矛盾。
例如收购被标为攻击式的 `process/targeted`，不能通过收购谓词的结构约束。

Frame 是结构分类，不是完整领域类型系统。`transfer` 不能单独证明所有权转移，
更不能推断质押、收入、价格或影响方向；这些语义须由谓词或外围领域模型定义。
完整实体类型、属性 schema 等约束仍未实现。

## 3. 修复二：先判身份，再投影状态；状态不等于真值

身份回答“是不是同一件事”，状态回答“关于这件事，观察报告了怎样的演变”。
生命周期不再给身份评分加分；`lifecycle_weight` 仅保留旧配置兼容。
否认、可能、预测、否定等限定词不能混入 `current_qualifiers` 并覆盖生命周期。

`StateProjection` 保存：

- `values`：可投影的生命周期值。
- `supporting_event_uuids`：每个值的支持观察。
- `observations`：所有原限定词、观察/情报 UUID、有效时间、到达时间。
- `conflicts`、`unknown`：争议和不能确定的迁移。
- `assertion_status`：`unverified` 或 `disputed`，不是已核实的事实标签。

只投影事件作用域的 `phase`、`intention`、`authorization`、`directive`。
意图、授权、指令按 `Qualifier.by` 分主体计算；不同主体不互相覆盖。
各主体最终值不同则不输出一个统一值，并记录争议。

有效时间优先级：限定词自己的 `time`，否则事件的
`effective_time → event_time → start_time`。明确给出但无法解析的时间保持未知，
不拿 `observed_at` 或预期时间补齐。日期表示日期精度，不能冒充精确时刻；
当前状态排序用其范围下界，同一天不同状态仍可能形成争议。

状态按有效时间重算，而不是最后到达覆盖：迟到的旧 `ongoing` 报道不会覆盖后来有效的
`completed`。`LifecycleSpec.transitions` 声明合法迁移，可用图可达性跨越未报道步骤，
但不生成中间事实。非法回退保留原合法状态并记录争议；同一有效时间出现不同值，
不任意挑选；缺有效时间或迁移配置时不确认不同值之间的先后迁移。

事件作用域的主张门控目前采用显式白名单：

- polarity：`positive/affirmative/true`。
- epistemic：`asserted/certain/confirmed/verified/factual/known`。
- modality：`actual/factual/asserted`。

这些维度出现白名单外的值，会阻止该观察的生命周期进入肯定状态投影，原限定词仍保留。
未提供这些限定词不自动核实真值。肯定状态与否认/否定并存会标记争议，
而不是因此把观察拆成不同事件。尚未实现来源可信度裁决和完整 Claim/Evidence 系统。

## 4. 修复三：缺失不是匹配，分数不能补偿必备证据

原设计已有 `UNKNOWN`，但若只降低分数，仍可能被其它维度抵消。
现在将“证据是否足够”与“已有证据得分”分开。

| IdentitySpec 配置 | 用途 |
| --- | --- |
| `identity_roles` | 参与身份评分的角色 |
| `auto_merge_required_roles` | 自动合并必须齐全且集合一致；默认全部身份角色 |
| `discriminator_roles` | 双方已知但不同则阻止合并，不靠它们补分 |
| `identity_attributes` | 只有声明的属性参与身份比较 |
| `auto_merge_required_attributes` | 自动合并不能缺失的身份属性 |
| `auto_merge_require_time` | 即使时间权重为零，也要求可比较时间 |
| `min_evidence_coverage` | 已比较证据的加权覆盖率下限，默认 0.75 |
| `max_time_uncertainty` | 时间精度范围上限，过粗则未知 |

计分只包含启用的角色、时间、地点、身份属性维度，按有效权重归一化。
缺失不产生正分，coverage 只衡量可比较证据比例；必备证据门槛独立于分数。
角色部分重叠不是完整身份相同，即使有部分得分也不能绕过必备角色检查。
未注册谓词可保留观察，但不能靠通用 fallback 自动合并到候选。

例：两条收购都有同一买方，但一条缺资产范围，不能仅凭买方、时间得高分后绑定。
卖方作为区分角色不同、已知身份属性不同、严格地点冲突或事件时间超窗，则属于明确不兼容。
金额若未声明为身份属性，其变化不决定身份。单位不同且未提供换算时为未知；
不能把“100 万”和“100 元”直接判成相同或不同。

时间比较保留年/月/日精度范围；近似时间不能作为精确证据。区间事件比较完整端点，
开放区间不凭空补结束时间。occurrence/interval 在启用时间权重时要求可比较时间；
strict 地点即使权重为零也须可比较。可重复事件不能只凭参与者相同跨日期合并。

新成员还与原成员逐一检查硬冲突；身份角色不取不同集合的并集。
这样避免 A 与 B 相邻、B 与 C 相邻，最终靠扩大时间/地点并集桥接本来不同的 A 与 C。

## 5. 解析 API 与兼容性

`resolve_canonical(...)` 返回 `tuple[CanonicalEvent | None, MatchResult]`：

- 无候选或全部候选判为不同：创建新 CanonicalEvent。
- 身份证据不足或候选领先差不足：返回 `(None, ambiguous)`，保留观察，不强制绑定，也不另造身份。
- 可合并：绑定后重算状态，实际投影值改变才返回 `state_update`，否则 `same_event`。
- 同一观察重复解析：返回已有对象及 `duplicate_observation`，不增加版本。
- 新成员与原成员有硬冲突：返回 ambiguous，不写入冲突成员。

`current_qualifiers` 保留为生命周期值的兼容视图；需要来源、主张与争议时读取
`state_projection`。核心入口默认空 Registry；兼容 `integration.service.EventEngine`
默认组合三个包，显式空配置仍保持为空。接入、分析、匹配使用同一 Registry。

新增 CanonicalEvent 的区分角色、身份属性和状态投影字段有默认值，旧数据并未自动迁移。
旧聚合若缺这些字段，应从原观察重建；以前自动绑定的成员需复核，不能声称本次已经
自动修正历史误合并。完整改绑审计、来源裁决、单位换算仍属于后续工作。

## 6. 验证

回归用例见 [test_semantic_fixes.py](tests/test_semantic_fixes.py)，覆盖缺角色、属性/单位、
时间精度与区间、迟到报道、主张限定、状态迁移、不同授权主体、Frame 约束、重复解析及桥接。
从 `event_engine/` 目录运行：

```bash
python -m unittest discover -s tests -v
python -X utf8 -m examples.basic_usage
python -X utf8 -m examples.file_ingestion_demo
```

本次验证：49 项测试通过，两个示例均通过；新增测试确认导入 schema 不加载 core、
领域配置、扩展、接入或存储模块。
