"""IIS Event V4 当前唯一的生产 Prompt（v48）及其校验协议。"""

from event_engine.extraction import (
    build_core_extraction_section,
    build_predicate_catalog,
    complete_core_extraction,
)
from ServiceComponent.IntelligenceHubDefines_v4 import (
    DEFAULT_EVENT_REGISTRY,
    RATE_LEVELS,
    map_rate_levels,
    validate_analysis_result_v4,
)
from ServiceComponent.intelligence_contracts import NonIntelligenceReason
from ServiceComponent.prompt_protocols import PromptCatalog, PromptProtocol

EXTRACTION_MODE_FULL = "full"
EXTRACTION_MODE_CORE_SUBSET = "core_subset"
EXTRACTION_MODE_CORE_SUBSET_LEVELS = "core_subset_levels"
EXTRACTION_MODE_CORE_SUBSET_LEVELS_REASON = "core_subset_levels_reason"


EVENT_V4_PREDICATE_CATALOG = build_predicate_catalog(DEFAULT_EVENT_REGISTRY)
EVENT_CORE_EXTRACTION_SECTION_MINIMAL_TIME = build_core_extraction_section(
    DEFAULT_EVENT_REGISTRY, minimal_time=True,
).replace("`event_extraction` 对象", "`EVENT_EXTRACTION` 字段的对象值", 1)


_IIS_ANALYSIS_REQUIREMENTS_NUMERIC_BASE = r"""# IIS 情报分析要求

先判断正文是否具有战略、战术、风险、市场、政策、安全或重大社会价值。文艺娱乐、普通体育、广告、生活指南、个人表达、纯历史回顾以及没有现实应用价值的纯学术内容通常无情报价值。

无情报价值时输出统一结构：
{"TAXONOMY":"NonIntelligence","SUB_CATEGORY":[],"REASON":"100字以内原因","RATE":{}}

有情报价值时沿用 IIS V2 字段，只增加 `EVENT_EXTRACTION`：

```json
{
  "TAXONOMY": "政治与安全",
  "SUB_CATEGORY": ["国防军事"],
  "REASON": "...",
  "EVENT_TITLE": "...",
  "EVENT_BRIEF": "...",
  "EVENT_TEXT": "...",
  "IMPACT": "...",
  "RATE": {
    "影响广度": 1,
    "影响深度": 1,
    "新颖性与异常性": 1,
    "演化与连锁潜力": 1,
    "舆情及认知影响": 1,
    "可行动性": 1
  },
  "TIPS": "",
  "EVENT_EXTRACTION": {}
}
```

- EVENT_TITLE：30字以内，只突出主事件。
- EVENT_BRIEF：100字以内，概括主事件及最多一项决定性关联事件。
- EVENT_TEXT：2000字以内，形成去重后的详细情报简报，不得添加正文未支持的事实。
- TAXONOMY：政治与安全、经济与金融、科技与网络、社会与环境之一。
- SUB_CATEGORY：1至5项，至少一项属于主分类。
- IMPACT：100字以内，只陈述正文支持的潜在影响。
- REASON：100字以内，说明分类和评分依据。
- TIPS：100字以内；无提示时使用空字符串。
- RATE：包含影响广度、影响深度、新颖性与异常性、演化与连锁潜力、舆情及认知影响、可行动性，均为1至10整数。
- 不得输出 TIME、LOCATION、GEOGRAPHY、PEOPLE、ORGANIZATION；这些速查字段由服务端从 EVENT_EXTRACTION 派生。
- 不得输出 kind、analysis、message、classification、assessment、raw_data 或 APPENDIX。
- 下方“事件抽取协议”的 JSON 示例只展示 `EVENT_EXTRACTION` 的对象值；最终输出时必须把它嵌入上述 IIS 顶层 JSON，不能单独输出事件抽取对象。

评分以 EVENT_EXTRACTION.primary_event_id 为锚点。相似历史消息已覆盖核心事实且正文没有新增事实时，新颖性、可行动性和演化与连锁潜力最高2分；回顾、总结、汇编且没有新增事实时，各维度最高4分。

主分类与子分类：
- 政治与安全：国际博弈、国内政局、国防军事、法律与合规、战略认知、重大犯罪与恐怖主义。
- 经济与金融：宏观经济、商业与市场、能源与资源、交通与物流、农业与粮食。
- 科技与网络：前沿科技、信息安全、数字基础设施。
- 社会与环境：社会民生、公共卫生、自然灾害与环境、教育与文化。
"""


def _build_rate_level_catalog(with_notes: bool = False) -> str:
    lines = []
    for dimension, levels in RATE_LEVELS.items():
        items = []
        for level, _score, gloss, note in levels:
            text = f"{level}（{gloss}）"
            if with_notes and note:
                text += f"【注意：{note}】"
            items.append(text)
        lines.append(f"- {dimension}：" + " < ".join(items))
    return "\n".join(lines)


IIS_ANALYSIS_REQUIREMENTS = (
    _IIS_ANALYSIS_REQUIREMENTS_NUMERIC_BASE
    .replace(
        '{"TAXONOMY":"NonIntelligence","SUB_CATEGORY":[],"REASON":"100字以内原因","RATE":{}}',
        '{"TAXONOMY":"NonIntelligence","SUB_CATEGORY":[],"NON_INTELLIGENCE_REASON":"原因码",'
        '"REASON":"100字以内具体原因","RATE":{}}',
    )
    .replace(
        '''  "RATE": {
    "影响广度": 1,
    "影响深度": 1,
    "新颖性与异常性": 1,
    "演化与连锁潜力": 1,
    "舆情及认知影响": 1,
    "可行动性": 1
  },''',
        '''  "RATE": {
    "影响广度": "local",
    "影响深度": "material",
    "新颖性与异常性": "uncommon",
    "演化与连锁潜力": "contained",
    "舆情及认知影响": "visible",
    "可行动性": "monitorable"
  },''')
    .replace(
        "- RATE：包含影响广度、影响深度、新颖性与异常性、演化与连锁潜力、舆情及认知影响、可行动性，均为1至10整数。",
        "- RATE：六个维度各从下方对应词表中选择一个等级词，不得输出数字分数。"
        "各词表按程度从左到右升序，第 1 级 unknown 表示正文证据不足；"
        "等级应根据正文可观察的事实选择，知名企业或国家的名称本身不构成高档证据。\n"
        + _build_rate_level_catalog(with_notes=True))
    .replace(
        "评分以 EVENT_EXTRACTION.primary_event_id 为锚点。相似历史消息已覆盖核心事实且正文没有新增事实时，新颖性、可行动性和演化与连锁潜力最高2分；回顾、总结、汇编且没有新增事实时，各维度最高4分。",
        "评级以 EVENT_EXTRACTION.primary_event_id 为锚点。相似历史消息已覆盖核心事实且正文没有新增事实时，"
        "新颖性、可行动性和演化与连锁潜力最高选第 2 级；回顾、总结、汇编且没有新增事实时，各维度最高选第 4 级。")
)

_EXTRACTION_ADDENDUM = """

## 信息状态与阶段（必须保留）

- 报道强调某方未证实、未回应、否认或拒绝置评时，必须保留该信息状态：对相关事件加 epistemic 限定词（value 用 doubted 或 denied，by 填该方实体）；“截至发稿未证实”按 doubted 记录。
- 事件阶段必须标注 phase 限定词（planned/ongoing/completed 等）；不得把进行中或计划中的事件写成已完成的确定事实。
- 否定信息（某事未发生、被取消、被否认）用 polarity=negated 限定词表达，不得直接省略。
"""


# V46：低价值结果增加机器可统计的原因码。保留 REASON 作为具体说明，使 IIS 能
# 区分抓取质量缺陷与“内容有效但情报价值低”，而不需要爬虫侧反馈闭环。
_NON_INTELLIGENCE_REASON_GUIDE = """

## 无情报价值原因分类（必须遵守）

无情报价值时必须输出：
{"TAXONOMY":"NonIntelligence","SUB_CATEGORY":[],"NON_INTELLIGENCE_REASON":"原因码","REASON":"100字以内具体原因","RATE":{}}

`NON_INTELLIGENCE_REASON` 只能选择下列一个原因码：
- empty_or_too_short：正文为空、过短或没有足以判断的正文
- anti_bot_or_access_denied：抓到验证码、访问拒绝、登录墙或其他反爬页面
- navigation_or_template：主要是导航、页眉页脚、栏目列表等页面模板
- media_stub：只有视频/音频入口、图片说明或极短媒体简介，没有有效正文
- garbled_or_incomplete：乱码、截断、关键段落缺失或正文结构明显损坏
- advertising_or_promotion：广告、软文、促销或纯品牌宣传
- entertainment_or_lifestyle：文艺娱乐、生活方式或消费指南
- sports：普通体育赛况、赛程或人物花边
- historical_or_background：纯历史回顾或背景资料，没有当前信息增量
- opinion_or_personal_expression：个人表达或缺少事实增量的评论
- routine_or_no_information_gain：内容有效，但属于例行信息或没有现实信息增量
- academic_without_current_relevance：纯学术内容，缺少当前现实应用价值
- duplicate_or_recap：正文只是已知消息的重复、摘要或汇编，没有新增事实
- other_non_intelligence：确属其他低价值原因；只有以上均不适用时才能选择

前五项表示抓取内容质量缺陷；其余表示正文有效但情报价值低。不得用内容题材原因掩盖反爬、空正文、模板页或媒体占位内容。
"""
def build_event_v48_prompt(registry=DEFAULT_EVENT_REGISTRY) -> str:
    """按指定 Registry 构造缓存友好的 v48 Prompt。"""
    extraction = build_core_extraction_section(registry, minimal_time=True).replace(
        "`event_extraction` 对象", "`EVENT_EXTRACTION` 字段的对象值", 1)
    return f"""# 角色与任务

你是专业情报分析师。分析当前正文的信息增量，完成 IIS 情报判断，并使用 Event Engine 核心子集协议提取一至三项核心事件。

输出语言：简体中文

# 输出总则

1. 最终只输出一个合法 JSON 对象，不得输出 Markdown、注释、推理过程或额外文字。
2. 所有事实必须来自当前正文；相似历史消息只能用于判断重复和信息增量。
3. 无法确定的可选字段直接省略，不得输出空对象或无意义的“未知”。
4. 输出必须符合 AnalysisResultV4；保持 V2 顶层字段，并将事件抽取对象放入 `EVENT_EXTRACTION`。
5. 有价值结果必须同时包含完整的 IIS 顶层字段和 `EVENT_EXTRACTION`；无价值结果不得包含 `EVENT_EXTRACTION`。

{IIS_ANALYSIS_REQUIREMENTS}

{extraction}{_EXTRACTION_ADDENDUM}
{_NON_INTELLIGENCE_REASON_GUIDE}"""


EVENT_ANALYSIS_PROMPT_V48 = build_event_v48_prompt()
assert '"normalized":"2026-10-17","surface":"原文时间"' in EVENT_ANALYSIS_PROMPT_V48


def build_event_v4_protocols(registry=DEFAULT_EVENT_REGISTRY) -> list[PromptProtocol]:
    """Event V4 的内置协议族。registry 参数化以便接入方使用自定义 Registry。"""

    def validate_full(raw):
        return validate_analysis_result_v4(raw)

    def validate_core_subset(raw):
        if isinstance(raw, dict) and raw.get("EVENT_EXTRACTION") is not None:
            completed = complete_core_extraction(raw["EVENT_EXTRACTION"], registry)
            raw = dict(raw)
            raw["EVENT_EXTRACTION"] = completed.model_dump(mode="python")
        return validate_analysis_result_v4(raw)

    def validate_core_subset_levels(raw):
        """v43 协议：先桥接事件子集，再把 RATE 等级词映射为分数。"""
        if not isinstance(raw, dict):
            return validate_analysis_result_v4(raw)
        raw = dict(raw)
        if raw.get("EVENT_EXTRACTION") is not None:
            completed = complete_core_extraction(raw["EVENT_EXTRACTION"], registry)
            raw["EVENT_EXTRACTION"] = completed.model_dump(mode="python")
        rate = raw.get("RATE")
        if rate:  # NonIntelligence 分支 RATE 为 {}，不映射
            raw["RATE"] = map_rate_levels(rate)
        return validate_analysis_result_v4(raw)

    def validate_core_subset_levels_reason(raw):
        result = validate_core_subset_levels(raw)
        if (result.is_non_intelligence
                and result.NON_INTELLIGENCE_REASON in (None, NonIntelligenceReason.UNSPECIFIED)):
            raise ValueError("NonIntelligence result requires NON_INTELLIGENCE_REASON")
        return result

    return [
        PromptProtocol(
            name=EXTRACTION_MODE_FULL,
            validate_result=validate_full,
            description="完整 event-extraction/1.0 契约直出",
        ),
        PromptProtocol(
            name=EXTRACTION_MODE_CORE_SUBSET,
            validate_result=validate_core_subset,
            description="核心子集协议：LLM 输出子集，桥接层确定性补齐为完整契约",
        ),
        PromptProtocol(
            name=EXTRACTION_MODE_CORE_SUBSET_LEVELS,
            validate_result=validate_core_subset_levels,
            description="核心子集 + RATE 等级词映射：LLM 选等级词，分数由服务端确定",
        ),
        PromptProtocol(
            name=EXTRACTION_MODE_CORE_SUBSET_LEVELS_REASON,
            validate_result=validate_core_subset_levels_reason,
            description="核心子集 + RATE 等级词映射 + 低价值结构化原因",
        ),
    ]


def build_event_v4_prompt_catalog(registry=DEFAULT_EVENT_REGISTRY) -> PromptCatalog:
    """构造只包含当前 v48 的 Event V4 Prompt 目录。"""
    catalog = PromptCatalog()
    for protocol in build_event_v4_protocols(registry):
        catalog.register_protocol(protocol)
    catalog.register_prompt(
        48, build_event_v48_prompt(registry),
        protocol=EXTRACTION_MODE_CORE_SUBSET_LEVELS_REASON, sampling=True)
    return catalog


EVENT_V4_PROMPT_CATALOG = build_event_v4_prompt_catalog()

# 兼容导出：web 展示、benchmark 等既有消费者继续按版本表读取。
EVENT_ANALYSIS_PROMPT_TABLE = EVENT_V4_PROMPT_CATALOG.sampling_table()
# 兼容既有单 Prompt 消费者。
EVENT_ANALYSIS_PROMPT = EVENT_ANALYSIS_PROMPT_V48
EVENT_ANALYSIS_EXTRA_PROMPTS = EVENT_V4_PROMPT_CATALOG.extra_table()
PROMPT_EXTRACTION_MODES = EVENT_V4_PROMPT_CATALOG.modes_table()
