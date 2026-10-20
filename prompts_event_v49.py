"""独立构建的 IIS v49：命名章节 + 权威词表，不读取或改写任何历史 Prompt。

保持 v48 的 core_subset_levels_reason 输出协议。词表来自 Registry、RATE_LEVELS、
TAXONOMY_SUBCATEGORIES 与 NonIntelligenceReason；历史版本不参与构建。
"""

from event_engine.extraction import (
    build_compact_core_extraction_section,
    build_compact_predicate_catalog,
)
from event_engine.extraction.compact_prompt import CORE_EXTRACTION_EXAMPLE
from ServiceComponent.IntelligenceHubDefines_v4 import (
    DEFAULT_EVENT_REGISTRY,
    RATE_LEVELS,
    TAXONOMY_SUBCATEGORIES,
)
from ServiceComponent.intelligence_contracts import NonIntelligenceReason


TASK_AND_OUTPUT_RULES = """# 任务与输出总则

你是专业情报分析师：分析当前正文的信息增量，判断情报价值；有价值时完成分类、简报、评级与核心事件抽取。
只输出一个合法 JSON 对象，语言为简体中文；不得输出 Markdown、注释、推理过程或额外文字。
所有事实必须来自当前正文；相似历史消息仅用于判断重复和信息增量。
无法确定的可选字段省略，不输出空对象或无意义的“未知”（低价值分支规定的 RATE={} 除外）。
不得输出 TIME、LOCATION、GEOGRAPHY、PEOPLE、ORGANIZATION 等派生速查字段，也不得输出 kind、analysis、message、classification、assessment、raw_data、APPENDIX。
"""

VALUE_DECISION = """# 价值判断与输出分支

判断正文是否具有战略、战术、风险、市场、政策、安全或重大社会价值。文艺娱乐、普通体育、广告、生活指南、个人表达、纯历史回顾及无现实应用价值的纯学术内容通常无情报价值。

## 无情报价值

输出以下结构，不含 EVENT_EXTRACTION：
{"TAXONOMY":"NonIntelligence","SUB_CATEGORY":[],"NON_INTELLIGENCE_REASON":"原因码","REASON":"100字以内具体原因","RATE":{}}

NON_INTELLIGENCE_REASON 选择下面一个原因码。前五项表示抓取内容质量缺陷，其余表示正文有效但情报价值低；不得用题材原因掩盖反爬、空正文、模板页或媒体占位内容。
"""

# 描述属于新版 Prompt 的独立文案，机器原因码引用共享契约。
NON_INTELLIGENCE_DESCRIPTIONS = (
    (NonIntelligenceReason.EMPTY_OR_TOO_SHORT, "正文为空、过短或没有足以判断的正文"),
    (NonIntelligenceReason.ANTI_BOT_OR_ACCESS_DENIED, "验证码、访问拒绝、登录墙或其他反爬页面"),
    (NonIntelligenceReason.NAVIGATION_OR_TEMPLATE, "主要是导航、页眉页脚、栏目列表等页面模板"),
    (NonIntelligenceReason.MEDIA_STUB, "仅视频/音频入口、图片说明或极短媒体简介，没有有效正文"),
    (NonIntelligenceReason.GARBLED_OR_INCOMPLETE, "乱码、截断、关键段落缺失或正文结构明显损坏"),
    (NonIntelligenceReason.ADVERTISING_OR_PROMOTION, "广告、软文、促销或纯品牌宣传"),
    (NonIntelligenceReason.ENTERTAINMENT_OR_LIFESTYLE, "文艺娱乐、生活方式或消费指南"),
    (NonIntelligenceReason.SPORTS, "普通体育赛况、赛程或人物花边"),
    (NonIntelligenceReason.HISTORICAL_OR_BACKGROUND, "纯历史回顾或背景资料，没有当前信息增量"),
    (NonIntelligenceReason.OPINION_OR_PERSONAL_EXPRESSION, "个人表达或缺少事实增量的评论"),
    (NonIntelligenceReason.ROUTINE_OR_NO_INFORMATION_GAIN, "内容有效，但属于例行信息或没有现实信息增量"),
    (NonIntelligenceReason.ACADEMIC_WITHOUT_CURRENT_RELEVANCE, "纯学术内容，缺少当前现实应用价值"),
    (NonIntelligenceReason.DUPLICATE_OR_RECAP, "已知消息的重复、摘要或汇编，没有新增事实"),
    (NonIntelligenceReason.OTHER_NON_INTELLIGENCE, "其他低价值原因，仅在以上均不适用时选择"),
)

VALUABLE_OUTPUT = """## 有情报价值

必须包含以下完整顶层字段，EVENT_EXTRACTION 为内嵌对象，不能单独输出：
{
  "TAXONOMY": "政治与安全",
  "SUB_CATEGORY": ["国防军事"],
  "REASON": "...",
  "EVENT_TITLE": "...",
  "EVENT_BRIEF": "...",
  "EVENT_TEXT": "...",
  "IMPACT": "...",
  "RATE": {
    "影响广度": "local",
    "影响深度": "material",
    "新颖性与异常性": "uncommon",
    "演化与连锁潜力": "contained",
    "舆情及认知影响": "visible",
    "可行动性": "monitorable"
  },
  "TIPS": "",
  "EVENT_EXTRACTION": """ + CORE_EXTRACTION_EXAMPLE + """
}

字段要求：
- EVENT_TITLE：≤30字，只突出主事件。
- EVENT_BRIEF：≤100字，概括主事件及最多一项决定性关联事件。
- EVENT_TEXT：≤2000字，去重后的详细情报简报，不添加正文未支持的事实。
- IMPACT：≤100字，只陈述正文支持的潜在影响。
- REASON：≤100字，说明分类和评级依据。
- TIPS：≤100字，无提示时为空字符串。
- TAXONOMY：从下方主分类中选一项；SUB_CATEGORY：1至5项，至少一项属于主分类。
- RATE：六个维度各选对应词表中的一个等级词，不输出数字分数。
"""

RATING_RULES = """# 六维评级

以 is_primary=true 的主事件为评级对象。各维度词表从左到右升序，第 1 级 unknown 表示正文证据不足；根据正文可观察事实选择，知名企业或国家的名称本身不构成高档证据。
相似历史消息已覆盖核心事实且正文无新增事实时，新颖性、可行动性、演化与连锁潜力最高选第 2 级；回顾、总结、汇编且无新增事实时，各维度最高选第 4 级。
"""


def _build_rate_catalog() -> str:
    """保持完整等级顺序、释义和准入/排除提示，不向模型暴露映射分数。"""
    lines = []
    for dimension, levels in RATE_LEVELS.items():
        items = [
            f"{level}（{gloss}）" + (f"【注意：{note}】" if note else "")
            for level, _score, gloss, note in levels
        ]
        lines.append(f"- {dimension}：" + " < ".join(items))
    return "\n".join(lines)


def build_event_v49_sections(registry=DEFAULT_EVENT_REGISTRY) -> dict[str, str]:
    """按阅读顺序显式组合；章节可独立审阅、统计长度，不经过历史版本替换。"""
    reasons = "\n".join(
        f"- {reason.value}：{description}"
        for reason, description in NON_INTELLIGENCE_DESCRIPTIONS
    )
    taxonomy = "\n".join(
        f"- {category}：" + "、".join(sorted(subcategories)) + "。"
        for category, subcategories in TAXONOMY_SUBCATEGORIES.items()
    )
    return {
        "task": TASK_AND_OUTPUT_RULES,
        "non_intelligence": VALUE_DECISION + "\n" + reasons,
        "valuable_output": VALUABLE_OUTPUT,
        "taxonomy": "# 主分类与子分类\n\n" + taxonomy,
        "rating": RATING_RULES + "\n" + _build_rate_catalog(),
        "extraction": build_compact_core_extraction_section(registry),
        "predicates": (
            "# 谓词目录\n\n"
            "以下目录来自 Event Engine Registry，是谓词和角色的唯一规则源。\n"
            + build_compact_predicate_catalog(registry)
        ),
    }


def build_event_v49_prompt(registry=DEFAULT_EVENT_REGISTRY) -> str:
    return "\n\n".join(section.strip() for section in build_event_v49_sections(registry).values()) + "\n"


EVENT_ANALYSIS_PROMPT_V49 = build_event_v49_prompt()
