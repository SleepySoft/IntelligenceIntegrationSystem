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


INDUSTRY_PROMPT_VERSION = 1
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
        INDUSTRY_PROMPT_VERSION,
        build_industry_prompt(registry),
        protocol=INDUSTRY_PROTOCOL,
        sampling=True,
    )
    return catalog


INDUSTRY_PROMPT_CATALOG = build_industry_prompt_catalog()
INDUSTRY_ANALYSIS_PROMPT = INDUSTRY_PROMPT_CATALOG.text_for(INDUSTRY_PROMPT_VERSION)


__all__ = [
    "INDUSTRY_ANALYSIS_PROMPT",
    "INDUSTRY_DISAMBIGUATION",
    "INDUSTRY_PROMPT_CATALOG",
    "INDUSTRY_PROMPT_VERSION",
    "build_industry_prompt",
    "build_industry_prompt_catalog",
    "validate_industry_core_result",
]
