"""行业情报 Prompt、核心子集桥接和结果校验协议。"""

from __future__ import annotations

from typing import Any

from event_engine.extraction import build_core_extraction_section, complete_core_extraction
from ServiceComponent.prompt_protocols import PromptCatalog, PromptProtocol

from .schema import (
    DEFAULT_EVENT_REGISTRY,
    RATE_LEVELS,
    map_rate_levels,
    validate_industry_analysis_result,
)


INDUSTRY_BASELINE_PROMPT_VERSION = 1
INDUSTRY_PROMPT_V2_VERSION = 2
INDUSTRY_PROMPT_V3_VERSION = 3
INDUSTRY_PROMPT_V4_VERSION = 4
INDUSTRY_PROMPT_V5_VERSION = 5
INDUSTRY_PROMPT_V6_VERSION = 6
INDUSTRY_PROMPT_V7_VERSION = 7
INDUSTRY_PRODUCTION_PROMPT_VERSION = INDUSTRY_PROMPT_V2_VERSION
INDUSTRY_PROMPT_VERSION = INDUSTRY_PROMPT_V7_VERSION
INDUSTRY_PROTOCOL = "industry_core_subset_levels_v1"


def _build_rate_catalog() -> str:
    lines: list[str] = []
    for dimension, levels in RATE_LEVELS.items():
        choices = []
        for level, _score, gloss, note in levels:
            item = f"{level}（{gloss}）"
            if note:
                item += f"【{note}】"
            choices.append(item)
        lines.append(f"- {dimension}：" + " < ".join(choices))
    return "\n".join(lines)


INDUSTRY_DISAMBIGUATION = r"""
## 产业事件消歧（必须遵守）

1. 实际或计划产量变化使用 increase_output/reduce_output；可生产能力变化使用
   expand_capacity/reduce_capacity。不得把减产自动写成削减产能。
2. 暂停但可能恢复使用 suspend_production；永久结束设施运营使用 close_facility；
   新建独立设施使用 build_facility；改造既有设施提高能力使用 expand_capacity。
3. 产品仍在研发使用 develop，验证阶段使用 test，正式提供使用 release，从研发或验证
   进入商业销售、量产供货使用 commercialize。停止产品或技术路线使用 discontinue_product。
4. 公司“宣布某行动”时，核心事件是该行动，并用 phase/intention 和 epistemic 保留阶段与
   陈述主体；不得用 communicate 取代明确的产业行动。
5. layoff 只表示取消岗位或终止一批雇佣关系；招聘冻结、自然减员、外包调整不得自动归为裁员。
6. 收购意向、签约、监管批准和完成交割是 acquire 的不同阶段；出售方主动剥离业务用 divest；
   多方结合且没有单一收购方时才用 merge。
7. shift_business_focus 需要资本、资产、组织、产品或市场行动作为证据，并尽量同时填写
   source 与 destination；“聚焦创新”“拥抱AI”“推进全球化”等口号不能构成战略转变。
8. 一篇文章包含“裁员并增加AI投资”等多个独立行动时分别提取；只有正文明确表达因果时才建立
   causes/promotes 关系，不得自行推断资金或战略因果。
9. 专用谓词适用时不得退回 increase、decrease、terminate 等宽泛谓词。确实无法准确分类时使用
   predicate.id=null 和精确 gloss，不得强套近似谓词。
10. 数量、金额、比例、产量、裁员人数、交易金额和产能变化必须尽可能写入 attributes；
    原文同时给出变化前后数值时优先保留 value_before/value_after，而不是只写模糊程度。
"""


NON_INTELLIGENCE_GUIDE = r"""
## 无价值分支

以下内容通常不是行业情报：没有具体公司行动的行业评论；普通财报数字或股价变化；常规产品小改款；
奖项、展会亮相、品牌宣传；没有对象、投入、时间或执行动作的战略口号；无法归因的传闻；没有新增
进展的回顾、周报或汇编。

无价值时只输出：
{"TAXONOMY":"NonIntelligence","SUB_CATEGORY":[],"NON_INTELLIGENCE_REASON":"原因码","REASON":"100字以内具体原因","RATE":{}}

原因码只能选择：empty_or_too_short、anti_bot_or_access_denied、navigation_or_template、media_stub、
garbled_or_incomplete、advertising_or_promotion、historical_or_background、opinion_or_personal_expression、
routine_or_no_information_gain、duplicate_or_recap、no_company_action、unsupported_rumor、other_non_intelligence。
抓取缺陷必须优先使用对应缺陷码，不能用题材原因掩盖空正文、反爬页或模板页。
"""


def build_industry_prompt(registry=DEFAULT_EVENT_REGISTRY) -> str:
    extraction = build_core_extraction_section(registry, minimal_time=True).replace(
        "`event_extraction` 对象", "`EVENT_EXTRACTION` 字段的对象值", 1)
    return f"""# 角色与任务

你是公司行动与产业投资情报分析师。识别公司真实发生、正式决定、明确计划或被可靠报道的经营行动，
判断它是否刷新了对公司经营状态、战略方向、资产配置、组织变化或投资逻辑的认识。

允许保留具有商业意义的故事性信息，例如大规模裁员、核心高管突然离任、工厂关闭、项目取消、
战略反转和内部重组；不得收录与经营无关的猎奇内容。

输出语言：简体中文

# 输出总则

1. 最终只输出一个合法 JSON 对象，不得输出 Markdown、注释、推理过程或额外文字。
2. 所有事实只能来自当前正文；相似历史消息只用于判断重复和信息增量。
3. 不得根据公司名称或知名度推断公司重要性、市场份额或行业排名。
4. 不得推断正文没有说明的产业链、供应链依赖或网络传播影响。
5. 不得输出 domain、total_score、公司重要性分、产业链分、APPENDIX 或其他系统字段。
6. 计划、签约、批准、执行中、已完成、取消、未证实和否认必须严格区分。
7. 有价值结果提取一至三项决定性事件；无价值结果不得包含 EVENT_EXTRACTION。

# 判断顺序

依次判断：正文是否有明确公司；是否有具体行动；行动是否改变产品、产量、产能、设施、人员、资产、
市场或战略方向；正文提供了什么新增事实；这些事实能否修正收入、成本、资本开支、竞争力、风险或
管理状态的认识；是否存在值得关注的组织、人事、冲突或反常信号。

没有具体公司行动，或者行动只有无法归因的传闻时，输出 NonIntelligence。

# 有价值输出结构

```json
{{
  "TAXONOMY": "行业情报",
  "SUB_CATEGORY": ["产量与产能"],
  "REASON": "100字以内，说明信息增量与评级依据",
  "EVENT_TITLE": "30字以内，只描述公司和核心行动",
  "EVENT_BRIEF": "100字以内，概括行动、规模、阶段和关键对象",
  "EVENT_TEXT": "2000字以内的去重事实简报，不得补充正文未支持的事实",
  "IMPACT": "100字以内，只说明正文事实可支持的经营或投资含义",
  "RATE": {{
    "行动实质性": "material",
    "规模与力度": "substantial",
    "事实确定性": "official",
    "认知增量": "meaningful",
    "预期差与异常性": "surprising",
    "经营与战略影响": "material",
    "投资可用性": "thesis_input",
    "关注度与故事性": "compelling"
  }},
  "TIPS": "100字以内的后续观察变量，不得给出买卖或目标价建议；无则为空字符串",
  "EVENT_EXTRACTION": {{}}
}}
```

- TAXONOMY 只能是“行业情报”。
- SUB_CATEGORY 选择一至三项：产品与技术、产量与产能、设施与扩张、组织与人事、并购与资产、
  财务困境、战略与市场。
- RATE 每个维度只能选择下方对应等级词，不得输出数字。
- “关注度与故事性”只评价与经营相关的具体冲突、转折、人事或组织信号；猎奇本身不能升档。
- “投资可用性”只评价能否修正经营假设，不得输出投资建议。

# 评级词表

{_build_rate_catalog()}

{NON_INTELLIGENCE_GUIDE}

{INDUSTRY_DISAMBIGUATION}

{extraction}

## 信息状态与阶段

- 未证实、匿名消息、公司未回应、公司否认或拒绝置评，必须使用 epistemic 限定词并填写 by。
- 未来或拟议事件使用 intention=planned 或 phase=planned/not_started；预期时间写 expected_start_time。
- 否定、取消或被否认的行动必须用 polarity/phase/epistemic 保留，不能直接省略。
- 公告是证据来源，不等于行动已经完成；官方披露可提高事实确定性，但事件阶段仍按正文填写。
"""


def _replace_once(text: str, old: str, new: str, *, section: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"industry prompt section {section!r} is not unique")
    return text.replace(old, new, 1)


INDUSTRY_V2_TASK = """你是产业决策信号与公司经营情报分析师。识别三类可改变判断的事实：公司真实发生、
正式决定或明确计划的经营行动；显著改变企业经营状态的结果或冲击；具有量化目标、实施机制或执行
进展的产业政策与监管行动。判断它是否刷新了对公司经营、产业投入、竞争格局或投资逻辑的认识。

允许保留具有商业意义的故事性信息，例如大规模裁员、核心高管突然离任、工厂关闭、项目取消、
战略反转和内部重组；不得收录与经营无关的猎奇内容。"""


INDUSTRY_V2_JUDGMENT = """依次判断：正文属于公司行动、经营状态冲击、经营相关冲突还是产业政策；是否有明确主体与具体事实；是否改变
产品、产量、产能、设施、人员、资产、经营结果、市场规则或产业投入方向；正文提供了什么新增事实；
这些事实通过什么可验证路径影响收入、成本、资本开支、需求、供给、竞争力、风险或管理状态；是否
存在值得关注的组织、人事、冲突或反常信号。

公司行动只有无法归因的传闻时输出 NonIntelligence。没有具体公司行动时，以下三类仍可进入有价值
分支：有量化变化或明确比较基线的经营状态冲击；具名公司确认、存在具体行为和公司应对、且与经营
或品牌风险直接相关的冲突事件；正式产业政策同时包含明确产业对象以及量化目标、实施机制、资源安排
或可核验执行进展之一。孤立普通客诉、未经证实指控、只有愿景口号的一般评论或没有新增进展的政策
复述仍输出 NonIntelligence。"""


INDUSTRY_V2_CALIBRATION = r"""
## 跨类型评级校准（逐维独立判断）

先确定主要信号类型，但不得把类型本身当成分数：

- 公司行动：投资、扩产、减产、裁员、并购、关厂、产品商业化等资源或经营承诺。
- 经营状态冲击：业绩断崖、历史低点、门店或订单显著收缩、违约、破产等已发生结果。它不是主动
  行动，但可因真实改变经营认识而成为高价值情报；提取实际结果，不得虚构公司决定。
- 经营相关冲突：经具名公司或可靠文件确认的冒名、勒索、欺诈、诉讼、内部冲突或品牌危机，以及
  公司的具体应对。它可以因故事性进入情报，但没有经营结果证据时不得冒充核心经营变化。
- 产业政策：政府或监管机构正式改变规则、投入方向或产业目标。只有宏观愿景不升档；量化目标、
  明确机制、受影响环节和执行进展越完整，决策价值越高。

八个维度必须分别寻找证据，禁止因“官方发布”“数字很大”“题材热门”而整组同档：

1. 行动实质性评价事实对现实状态的约束，不评价文章篇幅。发布声明、报警、财报披露只是证据或
   应对动作；应评价其揭示的经营变化。单一局部应对通常为 concrete。已改变重要人员、资产、设施、
   产销或经营状态，才可为 material。产业政策须至少有量化目标，并同时具有实施机制、资源安排或
   可核验执行进展，才可为 material；只有目标或口号为 rhetoric/concrete。
2. 规模与力度必须相对主体的核心业务或相关产业判断。孤立门店、单次客诉或单一局部事件通常不高于
   limited；覆盖核心业务、多地区或产业主要环节且正文有数量、金额、比例或设施证据，才可为
   large/massive。金额或目标大但没有作用范围，不得自动升档。
3. 事实确定性只评价证据状态。政府、公司、法院或交易所正式文件可为 official，但计划仍是计划；
   只有行动或结果已经发生并有结果证据，才可为 realized。
4. 认知增量评价正文相对其明确提供的既有状态增加了什么。新低、方向改变、首次量化目标、由计划
   进入执行等可升档；背景介绍、旧数据堆砌和没有进展的复述不得升档。
5. 预期差与异常性必须有正文中的比较基线。正文未提供基线时用 unknown，不得擅自判为 expected；
   expected 只用于正文明确说明是既定计划、惯例或延续。历史低点、突然逆转或与先前承诺相反可升档。
6. 经营与战略影响必须写出“事实→经营变量”的直接路径。局部品牌舆情或处理成本通常为 local；
   改变核心收入、成本、资本开支、供需、产能或竞争位置可为 material/major。产业政策若同时给出
   量化产业目标、落地机制和执行主体或进度，可按其真实覆盖范围评价，不因主体不是公司而降档。
7. 投资可用性评价能否修正并持续跟踪具体变量。只有泛泛风险提示为 background/watchlist；能修正
   收入、成本、资本开支、供需或风险假设为 thesis_input；同时具备量化目标、明确传导路径和近期
   可验证里程碑，才可为 decision_relevant。不得把“有趣”或“有代表性”写成投资价值。
8. 关注度与故事性独立评价经营相关的冲突、转折和人事信号。它可以提高故事价值，但不得反向抬高
   行动实质性、经营影响或投资可用性。

校准锚点：

- 单一品牌遭冒名招募、删评索费并报警：可有较高确定性、异常性和故事性；若正文没有收入、门店、
  核心运营或行业范围结果，行动实质性通常为 concrete，经营影响通常为 local，投资可用性通常为
  watchlist，不得仅凭“行业代表性”升为 material 或 decision_relevant。
- 正式产业行动方案若同时给出产业规模、设施或产量目标、受影响环节和已有执行进度，可达到
  material/large/major/decision_relevant；但计划阶段仍须如实标注，事实确定性不得冒充已经实现。
- 财报揭示利润历史低点并伴随核心门店、产量或订单显著收缩，属于经营状态冲击。即使没有新的主动
  决策，也可在规模、认知增量、经营影响和投资可用性上升档；普通季度波动仍按 routine/incremental。
"""


INDUSTRY_V3_TASK = """你是产业决策信号与公司经营情报分析师。只保留能够改变对经营状态、资源配置、
战略方向、产业投入、竞争格局或投资变量判断的新增事实。信号可来自公司行动、经营状态冲击、经营
相关冲突或可执行产业政策；不得因为主体知名、数字很大、官方发布或题材热门而自动收录或升档。

允许保留具有商业意义的故事性信息，例如大规模裁员、核心高管突然离任、工厂关闭、项目取消、
战略反转和内部重组；故事性只影响关注价值，不得替代经营证据。"""


INDUSTRY_V3_JUDGMENT = """按以下顺序判断，不得跳步：

1. 先排除空正文、模板页、宣传稿、无新增进展的回顾、无法归因的传闻和纯观点。
2. 确定唯一主要信号类型及决定性新增事实：
   - 公司行动：必须有明确对象，以及资源承诺、执行动作、阶段变化或结果之一；
   - 经营状态冲击：核心经营指标须有明确比较基线和显著变化，或量化资源配置变化并能连接经营变量；
   - 经营相关冲突：须有具名可靠来源、具体行为或公司应对，并能指出受影响的经营变量；
   - 产业政策：须为正式政策，有明确产业对象，并至少具备量化目标、实施机制、资源安排或执行进展之一。
3. 写出“新增事实→收入、成本、资本开支、供需、能力、风险或竞争位置”的直接路径。只有泛泛可能性
   而没有正文证据时降档；四类门槛均不满足时输出 NonIntelligence。

财报是信息载体，不是自动收录或自动排除理由。仅有例行数字、股价反应或延续既有趋势时属于普通
财报；出现盈利转折、历史极值、显著偏离目标，或研发、资本开支、人员、产能等资源配置发生量化
变化并影响经营判断时，可作为经营状态冲击。"""


INDUSTRY_V3_OUTPUT_AND_EVENT_RULES = r"""
## 解释与事件选择

- REASON 必须按“信号类型；决定性新增事实；影响的经营或投资变量；最关键限制”组织，具体说明为何
  收录及为何没有更高评级。不得只复述标题，不得使用“意义重大”“值得关注”等空泛结论。
- 有价值结果只保留一至三项决定性事件：先选主要经营变化，再选导致它的资源配置或公司行动，最后
  才是可验证承诺。背景数据、一般行业趋势和无直接关系的政策不得挤占事件名额。
- 汇编文章围绕同一决策信号选事件；不得为了凑满三项混入无关公司或宏观背景。存在具体行动或状态
  变化时，不得用 communicate 代替真实谓词。
"""


INDUSTRY_V4_OUTPUT_GUARDRAILS = r"""
## 输出前强制自检

- REASON 必须在 100 个字符以内（包含标点）。用分号压缩表达“信号类型、决定性新增事实、受影响
  变量、关键限制”，不得扩写背景；输出前必须检查长度，超过时删减修饰和重复事实。
- roles 的每个值都必须是实体对象数组，即使只有一个实体也写成 `[{"name":"...","type":"..."}]`；
  禁止把实体对象直接作为角色值，金额、数量等数值必须写入 attributes，不得充当角色实体。
- time、context、attributes 没有内容时必须省略整个键，禁止输出空对象 `{}`。qualifiers 和 relations
  没有内容时仍输出空数组。
"""


INDUSTRY_V5_FIRST_PASS_GUARDRAILS = r"""
## 首次校验通过自检

保持上述判断、评级和事件选择不变，仅按以下更保守的输出预算生成 JSON：

- EVENT_TITLE 最多 24 个字符；REASON、EVENT_BRIEF、IMPACT、TIPS 各最多 80 个字符；
  EVENT_TEXT 最多 1600 个字符。中文、英文、数字、空格和标点都各计一个字符；不能确定长度时继续缩短，
  优先删除背景、形容词和重复数字，不得删除决定性事实、阶段和关键限制。
- NonIntelligence 的 REASON 同样最多 80 个字符，只保留“内容类型、缺少的准入证据、结论”。
- 输出前逐项检查必填字段、字符预算和 JSON 类型；检查过程不得输出。字段超过预算时先改写再输出，
  禁止依赖接收端截断。
- epistemic 限定词必须带非空 by 实体对象数组。正文使用“知情人士”“公司”“法院”等称谓时，
  by 使用该原文称谓对应的实体；无法从正文确定陈述主体时不得编造主体或把不确定事实写成已确认：
  若它是决定性事实则输出 NonIntelligence，否则排除该非决定性事件。phase、polarity 禁止携带 by。
- qualifiers、relations 必须是数组；roles 的每个值和 qualifier.by 都必须是非空实体对象数组。
  time、context、attributes 要么省略，要么包含至少一个有效字段，禁止空对象和空数组冒充对象。
"""


INDUSTRY_V6_CLOSED_SCHEMA_GUARDRAILS = r"""
## 闭集 Schema 自检

保持 v4 的准入、评级、事件选择和字段长度不变。生成 JSON 前只检查以下闭集规则：

- 有价值结果的顶层键只能是 TAXONOMY、SUB_CATEGORY、REASON、EVENT_TITLE、EVENT_BRIEF、
  EVENT_TEXT、IMPACT、RATE、TIPS、EVENT_EXTRACTION。禁止新增 SUB_CATEGORY_NOTE、备注、说明等
  任何键，即使其值为 null 或空字符串也禁止输出。
- attributes 只能使用 amount、quantity、ratio、value_before、value_after、delta、duration、level；
  “用途”“原因”“备注”“描述”等文本不得自造为属性键，应保留在事件 surface 或正文简报中。
- amount.type 只能是 money，ratio.type 只能是 ratio，duration.type 只能是 duration；quantity、
  value_before、value_after、delta、level 的 type 只能是 number。
- 实体 type 必须取自后文协议列出的闭集。市场、赛道、行业等概念使用 topic，禁止输出 market、
  industry、company 等未登记 type；公司仍使用 organization。
"""


INDUSTRY_V7_OUTPUT_RELIABILITY_GUARDRAILS = r"""
## 输出最终检查

- 不得为满足长度或 Schema 改变 TAXONOMY、RATE、决定性事实或事件阶段；超长时只压缩表达。
- 每个 predicate 必须含 surface；frame 只用当前协议字段，禁止旧字段 agentive 或把 frame 放入 predicate。
- roles 值必须是非空实体数组，数值只写 attributes；空的可选对象省略；不得自造协议外的键或类型。
"""


INDUSTRY_V3_CALIBRATION = r"""
## 保守评级：逐维从低档开始

每个维度先选择正文证据能够支持的最低合理档，再逐级检查升档条件。八维独立判断；一个强证据不能
整组抬分。没有明确证据时使用 unknown/none/background 等低档，不得用“行业重要”“公司知名”补证。

1. 行动实质性：披露、评论和报警本身通常不高于 concrete。只有人员、资产、设施、产销、核心指标
   或约束性政策已经发生显著状态变化，才可 material；根本转型、清算或存续变化才可 transformative。
2. 规模与力度：substantial 必须有金额、人数、比例、产量或设施证据且对相关业务明显；large 还须
   覆盖核心业务、多设施或多地区；massive 仅用于接近全公司或产业核心范围，不按绝对数字自动判定。
3. 事实确定性：正式文件最高先评 official；只有行动或结果已经发生并有结果证据才可 realized。
   计划、目标和预测即使由官方发布也不是 realized。
4. 认知增量：新增一个细节通常为 incremental；明确改变经营状态认识才可 meaningful；修正既有核心
   假设且正文给出比较基线才可 thesis_changing；fundamental 极少使用。
5. 预期差与异常性：没有正文比较基线必须 unknown，不得擅自判 expected。surprising 以上须有历史值、
   原目标、先前承诺或明确常态作为基线；普通波动不得升档。
6. 经营与战略影响：必须写出直接传导路径。局部团队、门店、产品通常为 local；影响核心收入、成本、
   资本开支、产能或竞争位置才可 material/major；existential 仅用于企业存续风险。
7. 投资可用性：能形成跟踪项为 watchlist；同时具备量化变量、因果路径和后续验证点才可 thesis_input；
   decision_relevant 还须影响核心假设并有近期可验证里程碑；time_sensitive 极少使用。
8. 关注度与故事性：只评价经营相关的冲突、转折和人事信号。它不得反向抬高行动、规模、经营影响或
   投资可用性，也不能使本应 NonIntelligence 的内容入选。
"""


def build_industry_prompt_v2(registry=DEFAULT_EVENT_REGISTRY) -> str:
    """在 v1 输出协议上增加跨类型价值判断与证据标尺。"""

    prompt = build_industry_prompt(registry)
    prompt = _replace_once(
        prompt,
        """你是公司行动与产业投资情报分析师。识别公司真实发生、正式决定、明确计划或被可靠报道的经营行动，
判断它是否刷新了对公司经营状态、战略方向、资产配置、组织变化或投资逻辑的认识。

允许保留具有商业意义的故事性信息，例如大规模裁员、核心高管突然离任、工厂关闭、项目取消、
战略反转和内部重组；不得收录与经营无关的猎奇内容。""",
        INDUSTRY_V2_TASK,
        section="role_and_task",
    )
    prompt = _replace_once(
        prompt,
        """依次判断：正文是否有明确公司；是否有具体行动；行动是否改变产品、产量、产能、设施、人员、资产、
市场或战略方向；正文提供了什么新增事实；这些事实能否修正收入、成本、资本开支、竞争力、风险或
管理状态的认识；是否存在值得关注的组织、人事、冲突或反常信号。

没有具体公司行动，或者行动只有无法归因的传闻时，输出 NonIntelligence。""",
        INDUSTRY_V2_JUDGMENT,
        section="judgment_order",
    )
    prompt = _replace_once(
        prompt,
        "以下内容通常不是行业情报：没有具体公司行动的行业评论；",
        "以下内容通常不是行业情报：没有具体公司行动、可核实经营冲突且没有可执行产业政策的行业评论；",
        section="non_intelligence_scope",
    )
    prompt = _replace_once(
        prompt,
        "# 评级词表\n\n",
        f"# 评级词表\n{INDUSTRY_V2_CALIBRATION}\n",
        section="rating_calibration",
    )
    return prompt


def build_industry_prompt_v3(registry=DEFAULT_EVENT_REGISTRY) -> str:
    """精简 v2 的跨类型能力，以硬准入和逐维升档证据形成更严格的 Shadow 候选。"""

    prompt = build_industry_prompt(registry)
    prompt = _replace_once(
        prompt,
        """你是公司行动与产业投资情报分析师。识别公司真实发生、正式决定、明确计划或被可靠报道的经营行动，
判断它是否刷新了对公司经营状态、战略方向、资产配置、组织变化或投资逻辑的认识。

允许保留具有商业意义的故事性信息，例如大规模裁员、核心高管突然离任、工厂关闭、项目取消、
战略反转和内部重组；不得收录与经营无关的猎奇内容。""",
        INDUSTRY_V3_TASK,
        section="role_and_task",
    )
    prompt = _replace_once(
        prompt,
        """依次判断：正文是否有明确公司；是否有具体行动；行动是否改变产品、产量、产能、设施、人员、资产、
市场或战略方向；正文提供了什么新增事实；这些事实能否修正收入、成本、资本开支、竞争力、风险或
管理状态的认识；是否存在值得关注的组织、人事、冲突或反常信号。

没有具体公司行动，或者行动只有无法归因的传闻时，输出 NonIntelligence。""",
        INDUSTRY_V3_JUDGMENT,
        section="judgment_order",
    )
    prompt = _replace_once(
        prompt,
        "# 有价值输出结构\n",
        f"{INDUSTRY_V3_OUTPUT_AND_EVENT_RULES}\n# 有价值输出结构\n",
        section="explanation_and_event_selection",
    )
    prompt = _replace_once(
        prompt,
        "以下内容通常不是行业情报：没有具体公司行动的行业评论；普通财报数字或股价变化；",
        "以下内容通常不是行业情报：未满足上述四类准入门槛的行业评论；普通且没有经营判断增量的财报数字或股价变化；",
        section="non_intelligence_scope",
    )
    prompt = _replace_once(
        prompt,
        "# 评级词表\n\n",
        f"# 评级词表\n{INDUSTRY_V3_CALIBRATION}\n",
        section="rating_calibration",
    )
    return prompt


def build_industry_prompt_v4(registry=DEFAULT_EVENT_REGISTRY) -> str:
    """在 v3 价值判断不变的前提下收紧可执行的输出边界。"""

    prompt = build_industry_prompt_v3(registry)
    return _replace_once(
        prompt,
        "# 有价值输出结构\n",
        f"{INDUSTRY_V4_OUTPUT_GUARDRAILS}\n# 有价值输出结构\n",
        section="output_guardrails",
    )


def build_industry_prompt_v5(registry=DEFAULT_EVENT_REGISTRY) -> str:
    """在 v4 语义不变的前提下为长度和限定词结构预留校验余量。"""

    prompt = build_industry_prompt_v4(registry)
    return _replace_once(
        prompt,
        "# 有价值输出结构\n",
        f"{INDUSTRY_V5_FIRST_PASS_GUARDRAILS}\n# 有价值输出结构\n",
        section="first_pass_guardrails",
    )


def build_industry_prompt_v6(registry=DEFAULT_EVENT_REGISTRY) -> str:
    """从正式 v4 派生，只封闭已观测到的 Schema 自造空间。"""

    prompt = build_industry_prompt_v4(registry)
    return _replace_once(
        prompt,
        "# 有价值输出结构\n",
        f"{INDUSTRY_V6_CLOSED_SCHEMA_GUARDRAILS}\n# 有价值输出结构\n",
        section="closed_schema_guardrails",
    )


def build_industry_prompt_v7(registry=DEFAULT_EVENT_REGISTRY) -> str:
    """从 v2 直接派生，只提高输出可靠性，不继承 v3-v6 的判断语义。"""

    prompt = build_industry_prompt_v2(registry)
    replacements = (
        ('"REASON":"100字以内具体原因"', '"REASON":"80字以内具体原因"',
         "non_intelligence_reason_budget"),
        ('"REASON": "100字以内，说明信息增量与评级依据"',
         '"REASON": "80字以内，说明信息增量与评级依据"', "reason_budget"),
        ('"EVENT_TITLE": "30字以内，只描述公司和核心行动"',
         '"EVENT_TITLE": "24字以内，只描述公司和核心行动"', "title_budget"),
        ('"EVENT_BRIEF": "100字以内，概括行动、规模、阶段和关键对象"',
         '"EVENT_BRIEF": "80字以内，概括行动、规模、阶段和关键对象"', "brief_budget"),
        ('"EVENT_TEXT": "2000字以内的去重事实简报，不得补充正文未支持的事实"',
         '"EVENT_TEXT": "1600字以内的去重事实简报，不得补充正文未支持的事实"', "text_budget"),
        ('"IMPACT": "100字以内，只说明正文事实可支持的经营或投资含义"',
         '"IMPACT": "80字以内，只说明正文事实可支持的经营或投资含义"', "impact_budget"),
        ('"TIPS": "100字以内的后续观察变量，不得给出买卖或目标价建议；无则为空字符串"',
         '"TIPS": "80字以内的后续观察变量，不得给出买卖或目标价建议；无则为空字符串"',
         "tips_budget"),
    )
    for old, new, section in replacements:
        prompt = _replace_once(prompt, old, new, section=section)
    return _replace_once(
        prompt,
        "# 有价值输出结构\n",
        f"{INDUSTRY_V7_OUTPUT_RELIABILITY_GUARDRAILS}\n# 有价值输出结构\n",
        section="v7_output_reliability_guardrails",
    )


def validate_industry_core_result(raw: Any, *, registry=DEFAULT_EVENT_REGISTRY):
    if not isinstance(raw, dict):
        return validate_industry_analysis_result(raw, registry=registry)
    normalized = dict(raw)
    if normalized.get("EVENT_EXTRACTION") is not None:
        completed = complete_core_extraction(normalized["EVENT_EXTRACTION"], registry)
        normalized["EVENT_EXTRACTION"] = completed.model_dump(mode="python")
    if normalized.get("RATE"):
        normalized["RATE"] = map_rate_levels(normalized["RATE"])
    return validate_industry_analysis_result(normalized, registry=registry)


def build_industry_prompt_catalog(registry=DEFAULT_EVENT_REGISTRY) -> PromptCatalog:
    catalog = PromptCatalog()
    catalog.register_protocol(PromptProtocol(
        name=INDUSTRY_PROTOCOL,
        validate_result=lambda raw: validate_industry_core_result(raw, registry=registry),
        description="行业公司行动判断 + 八维等级词 + Event Engine 核心子集",
    ))
    catalog.register_prompt(
        INDUSTRY_BASELINE_PROMPT_VERSION,
        build_industry_prompt(registry),
        protocol=INDUSTRY_PROTOCOL,
        # v1 是历史冻结基线，转正后不再接收新生产样本。
        sampling=False,
    )
    catalog.register_prompt(
        INDUSTRY_PROMPT_V2_VERSION,
        build_industry_prompt_v2(registry),
        protocol=INDUSTRY_PROTOCOL,
        # v2 于 2026-11-12 重新成为唯一正式版本，用于 25% 同模型 A/A 稳定性实验。
        sampling=True,
    )
    catalog.register_prompt(
        INDUSTRY_PROMPT_V3_VERSION,
        build_industry_prompt_v3(registry),
        protocol=INDUSTRY_PROTOCOL,
        # v3 保留失败样本和历史回看，不再作为当前 Shadow 候选。
        sampling=False,
    )
    catalog.register_prompt(
        INDUSTRY_PROMPT_V4_VERSION,
        build_industry_prompt_v4(registry),
        protocol=INDUSTRY_PROTOCOL,
        # v4 已完成生产与 v6 Shadow 评估；保留历史回看，不再接收新样本。
        sampling=False,
    )
    catalog.register_prompt(
        INDUSTRY_PROMPT_V5_VERSION,
        build_industry_prompt_v5(registry),
        protocol=INDUSTRY_PROTOCOL,
        # v5 首轮未改善首过率，仅保留历史回看。
        sampling=False,
    )
    catalog.register_prompt(
        INDUSTRY_PROMPT_V6_VERSION,
        build_industry_prompt_v6(registry),
        protocol=INDUSTRY_PROTOCOL,
        # v6 从正式 v4 派生，只由 Shadow 验证闭集 Schema 首过率。
        sampling=False,
    )
    catalog.register_prompt(
        INDUSTRY_PROMPT_V7_VERSION,
        build_industry_prompt_v7(registry),
        protocol=INDUSTRY_PROTOCOL,
        # v7 从正式 v2 直接派生，只验证长度预算和闭集输出可靠性。
        sampling=False,
    )
    return catalog


INDUSTRY_PROMPT_CATALOG = build_industry_prompt_catalog()
INDUSTRY_ANALYSIS_PROMPT = INDUSTRY_PROMPT_CATALOG.text_for(
    INDUSTRY_PRODUCTION_PROMPT_VERSION)


__all__ = [
    "INDUSTRY_ANALYSIS_PROMPT",
    "INDUSTRY_BASELINE_PROMPT_VERSION",
    "INDUSTRY_DISAMBIGUATION",
    "INDUSTRY_PROMPT_CATALOG",
    "INDUSTRY_PROMPT_VERSION",
    "INDUSTRY_PROMPT_V2_VERSION",
    "INDUSTRY_PROMPT_V3_VERSION",
    "INDUSTRY_PROMPT_V4_VERSION",
    "INDUSTRY_PROMPT_V5_VERSION",
    "INDUSTRY_PROMPT_V6_VERSION",
    "INDUSTRY_PROMPT_V7_VERSION",
    "INDUSTRY_PRODUCTION_PROMPT_VERSION",
    "build_industry_prompt",
    "build_industry_prompt_v2",
    "build_industry_prompt_v3",
    "build_industry_prompt_v4",
    "build_industry_prompt_v5",
    "build_industry_prompt_v6",
    "build_industry_prompt_v7",
    "build_industry_prompt_catalog",
    "validate_industry_core_result",
]
