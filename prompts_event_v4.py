"""IIS 情报分析 Prompt，由 Event Engine 抽取协议与 IIS 外层要求组合而成。

Prompt 通过 PromptCatalog 集中注册（协议族机制，见 ServiceComponent.prompt_protocols）：
- 协议（PromptProtocol）= 一族 prompt 共用的前处理/后处理/校验链；
  多个 prompt 版本可共用同一协议（小迭代），结构大改才新建协议；
- 版本注册时声明 sampling（是否参与生产随机抽样）；非 sampling 版本仅供
  显式选择（人工调试、影子链路、回滚通道）。
- 末尾的 *_TABLE / PROMPT_EXTRACTION_MODES 为兼容既有消费者的派生导出，
  新增 prompt 或协议时只需调用 register_protocol / register_prompt。
"""

from event_engine.extraction import (
    build_core_extraction_section,
    build_event_extraction_section,
    build_predicate_catalog,
    complete_core_extraction,
)
from ServiceComponent.IntelligenceHubDefines_v4 import (
    DEFAULT_EVENT_REGISTRY,
    validate_analysis_result_v4,
)
from ServiceComponent.prompt_protocols import PromptCatalog, PromptProtocol

EXTRACTION_MODE_FULL = "full"
EXTRACTION_MODE_CORE_SUBSET = "core_subset"


EVENT_V4_PREDICATE_CATALOG = build_predicate_catalog(DEFAULT_EVENT_REGISTRY)
EVENT_EXTRACTION_SECTION = build_event_extraction_section(DEFAULT_EVENT_REGISTRY).replace(
    "`event_extraction` 对象", "`EVENT_EXTRACTION` 字段的对象值", 1)
EVENT_CORE_EXTRACTION_SECTION = build_core_extraction_section(DEFAULT_EVENT_REGISTRY).replace(
    "`event_extraction` 对象", "`EVENT_EXTRACTION` 字段的对象值", 1)


IIS_ANALYSIS_REQUIREMENTS_V4 = r"""# IIS 情报分析要求

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


EVENT_ANALYSIS_PROMPT_V41 = f"""# 角色与任务

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
4. 输出必须符合 AnalysisResultV4；保持 V2 顶层字段，并将事件抽取对象放入 `EVENT_EXTRACTION`。
5. 有价值结果必须同时包含完整的 IIS 顶层字段和 `EVENT_EXTRACTION`；无价值结果不得包含 `EVENT_EXTRACTION`。

{IIS_ANALYSIS_REQUIREMENTS_V4}

{EVENT_EXTRACTION_SECTION}
"""


# V42：核心子集协议。LLM 不输出 schema_version、局部 ID、已登记谓词的 frame 和
# 限定词编号，实体内联在角色中，事件关系使用数组位置序号；服务端桥接层确定性补齐为
# 完整的 event-extraction/1.0 结果后再校验。设计见
# doc/20261013_event_v4_extraction_subset_design.md。
EVENT_ANALYSIS_PROMPT_V42 = f"""# 角色与任务

你是专业情报分析师。分析当前正文的信息增量，完成 IIS 情报判断，并使用 Event Engine 核心子集协议提取一至三项核心事件。

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
4. 输出必须符合 AnalysisResultV4；保持 V2 顶层字段，并将事件抽取对象放入 `EVENT_EXTRACTION`。
5. 有价值结果必须同时包含完整的 IIS 顶层字段和 `EVENT_EXTRACTION`；无价值结果不得包含 `EVENT_EXTRACTION`。

{IIS_ANALYSIS_REQUIREMENTS_V4}

{EVENT_CORE_EXTRACTION_SECTION}
"""


# 生产抽样表：v42（核心子集协议）已于 2026-10-12 切换为生产版本，
# 依据 doc/20261012_prompt_v41_v42_comparison.md 的对比结果（v42 零校验失败，
# v41 有 6 次机械类校验失败；NAZA 等重点案例结构显著改善）。
# v41 保留为回滚/对照通道（sampling=False），可经调试路由显式选择。


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
    ]


def build_event_v4_prompt_catalog(registry=DEFAULT_EVENT_REGISTRY) -> PromptCatalog:
    """注册 Event V4 全部 prompt 版本的目录；接入方扩展时复制本函数自行注册。"""
    catalog = PromptCatalog()
    for protocol in build_event_v4_protocols(registry):
        catalog.register_protocol(protocol)
    catalog.register_prompt(
        42, EVENT_ANALYSIS_PROMPT_V42,
        protocol=EXTRACTION_MODE_CORE_SUBSET, sampling=True)
    catalog.register_prompt(
        41, EVENT_ANALYSIS_PROMPT_V41,
        protocol=EXTRACTION_MODE_FULL, sampling=False)
    return catalog


EVENT_V4_PROMPT_CATALOG = build_event_v4_prompt_catalog()

# 兼容导出：web 展示、benchmark 等既有消费者继续按版本表读取。
EVENT_ANALYSIS_PROMPT_TABLE = EVENT_V4_PROMPT_CATALOG.sampling_table()
EVENT_ANALYSIS_PROMPT = EVENT_ANALYSIS_PROMPT_V42
EVENT_ANALYSIS_EXTRA_PROMPTS = EVENT_V4_PROMPT_CATALOG.extra_table()
PROMPT_EXTRACTION_MODES = EVENT_V4_PROMPT_CATALOG.modes_table()
