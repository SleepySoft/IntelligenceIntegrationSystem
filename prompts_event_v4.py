"""IIS 情报分析 Prompt，由 Event Engine 抽取协议与 IIS 外层要求组合而成。"""

from event_engine.extraction import build_event_extraction_section, build_predicate_catalog
from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY


EVENT_V4_PREDICATE_CATALOG = build_predicate_catalog(DEFAULT_EVENT_REGISTRY)
EVENT_EXTRACTION_SECTION = build_event_extraction_section(DEFAULT_EVENT_REGISTRY)


IIS_ANALYSIS_REQUIREMENTS_V4 = r"""# IIS 情报分析要求

先判断正文是否具有战略、战术、风险、市场、政策、安全或重大社会价值。文艺娱乐、普通体育、广告、生活指南、个人表达、纯历史回顾以及没有现实应用价值的纯学术内容通常无情报价值。

无情报价值时输出：
{"kind":"non_intelligence","reason":"100字以内原因"}

有情报价值时输出 `kind=valuable`，并完成以下外层字段：

```json
{
  "kind": "valuable",
  "message": {"title":"...","brief":"...","text":"..."},
  "classification": {"taxonomy":"政治与安全","subcategories":["国防军事"]},
  "assessment": {"impact":"...","reason":"...","rate":{},"tips":""},
  "event_extraction": {}
}
```

- message.title：30字以内，只突出主事件。
- message.brief：100字以内，概括主事件及最多一项决定性关联事件。
- message.text：2000字以内，形成去重后的详细情报简报，不得添加正文未支持的事实。
- classification.taxonomy：政治与安全、经济与金融、科技与网络、社会与环境之一。
- classification.subcategories：1至5项，至少一项属于主分类。
- assessment.impact：100字以内，只陈述正文支持的潜在影响。
- assessment.reason：100字以内，说明分类和评分依据。
- assessment.tips：100字以内；无提示时使用空字符串。
- assessment.rate：包含影响广度、影响深度、新颖性与异常性、演化与连锁潜力、舆情及认知影响、可行动性，均为1至10整数。

评分以 event_extraction.primary_event_id 为锚点。相似历史消息已覆盖核心事实且正文没有新增事实时，新颖性、可行动性和演化与连锁潜力最高2分；回顾、总结、汇编且没有新增事实时，各维度最高4分。

主分类与子分类：
- 政治与安全：国际博弈、国内政局、国防军事、法律与合规、战略认知、重大犯罪与恐怖主义。
- 经济与金融：宏观经济、商业与市场、能源与资源、交通与物流、农业与粮食。
- 科技与网络：前沿科技、信息安全、数字基础设施。
- 社会与环境：社会民生、公共卫生、自然灾害与环境、教育与文化。
"""


EVENT_ANALYSIS_PROMPT_V40 = f"""# 角色与任务

你是专业情报分析师。分析当前正文的信息增量，完成 IIS 情报判断，并使用 Event Engine 协议提取一至三项核心事件。

当前参考时间：{{{{CURRENT_DATE}}}}
输出语言：简体中文

# 输入

正文：
{{{{CONTENT}}}}

相似历史消息参考：
{{{{SIMILAR_MESSAGES}}}}

# 输出总则

1. 最终只输出一个合法 JSON 对象，不得输出 Markdown、注释、推理过程或额外文字。
2. 所有事实必须来自当前正文；相似历史消息只能用于判断重复和信息增量。
3. 无法确定的可选字段直接省略，不得输出空对象或无意义的“未知”。
4. 输出必须符合 AnalysisResultV4：valuable 结果由 message、classification、assessment、event_extraction 四个子结构组成。

{IIS_ANALYSIS_REQUIREMENTS_V4}

{EVENT_EXTRACTION_SECTION}
"""


EVENT_ANALYSIS_PROMPT_TABLE = {40: EVENT_ANALYSIS_PROMPT_V40}
EVENT_ANALYSIS_PROMPT = EVENT_ANALYSIS_PROMPT_V40
