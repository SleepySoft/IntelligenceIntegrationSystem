"""IIS v51：在 v48 基线上仅压缩谓词/角色章节。

保留 v48 的价值判断、评级、事件选择、时间、地点、属性、限定词、关系与
JSON 示例；只把重复展开的谓词目录替换为公共角色折叠目录，并用 v50 已验证的
角色白名单/实体类型纪律替换原角色说明。
"""

from event_engine.extraction import (
    build_compact_predicate_catalog,
    build_core_extraction_section,
    build_predicate_catalog,
)
from prompts_event_v50 import build_role_discipline


_ROLE_START = "## 角色\n"
_ROLE_END = "## 时间、地点和属性\n"


def build_compact_role_section(registry) -> str:
    """保留 v48 角色语义，同时采用 v50 的机械白名单检查。"""
    return """## 角色纪律（输出前逐项检查）

所有角色值都是非空实体对象数组。

""" + build_role_discipline(registry).replace(
        "### 已登记谓词的角色白名单（输出前逐项检查）\n\n", "", 1,
    ) + """
- predicate.id=null 时必须提供 frame；frame.topology 限定角色：intrinsic 用 subject；relational 用 subject、counterpart；targeted 用 actor、target，可选 instrument；transfer 用 theme，可选 source、destination、agent，且至少有 source 或 destination。
- 严格区分下令方、执行方和对象：“甲要求乙调查丙”中 investigator=乙、object=丙，甲只进入 directive 限定词的 by；调查、审查、评估的 object 必须是真正客体。
- 实体名保留消歧类别词，如“纪录片《NAZA》”“Rafael天然气项目”，不得只写裸专名。

"""


def build_event_v51_extraction_section(registry) -> str:
    """从 v48 抽取章节出发，只替换谓词目录和角色说明。"""
    section = build_core_extraction_section(registry, minimal_time=True)
    full_catalog = build_predicate_catalog(registry)
    compact_catalog = build_compact_predicate_catalog(registry)
    if section.count(full_catalog) != 1:
        raise ValueError("v48 extraction section has unexpected predicate catalog")
    section = section.replace(full_catalog, compact_catalog, 1)

    start = section.find(_ROLE_START)
    end = section.find(_ROLE_END, start)
    if start < 0 or end < 0:
        raise ValueError("v48 extraction section has unexpected role markers")
    return section[:start] + build_compact_role_section(registry) + section[end:]


def build_event_v51_prompt(base_v48_prompt: str, base_v48_extraction: str, registry) -> str:
    """在完整 v48 Prompt 中替换一个抽取章节，其他内容逐字保持。"""
    if base_v48_prompt.count(base_v48_extraction) != 1:
        raise ValueError("v48 prompt has unexpected extraction section")
    return base_v48_prompt.replace(
        base_v48_extraction, build_event_v51_extraction_section(registry), 1)
