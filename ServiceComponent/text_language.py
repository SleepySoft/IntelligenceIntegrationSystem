# ServiceComponent/text_language.py
# 语言判定启发式（Core 中立工具）。
#
# 历史：原位于 AsyncTranslationPatch.py，为让向量子系统等其它扩展在不依赖
# 翻译扩展实现的前提下复用同一判定逻辑，提取到本模块。
# 注意：原生中文文档不会带任何翻译标记，"是否需要翻译"只能通过本启发式判断。

import re

from typing import Optional, Dict, Any

_CJK_RE = re.compile(r'[一-鿿]')
_ASCII_RE = re.compile(r'[A-Za-z]')


def _ratio(pat, s: str) -> float:
    if not s:
        return 0.0
    return len(pat.findall(s)) / max(1, len(s))


def looks_non_zh(s: Optional[str], cjk_th=0.18, ascii_th=0.12) -> bool:
    """
    Heuristic, fast and local.
    """
    if not s:
        return False
    t = str(s).strip()
    if len(t) < 12:
        return (_ratio(_CJK_RE, t) < 0.05) and (_ratio(_ASCII_RE, t) > 0.2)
    return (_ratio(_CJK_RE, t) < cjk_th) and (_ratio(_ASCII_RE, t) > ascii_th)


def needs_translation(doc: Dict[str, Any]) -> bool:
    """
    Decide if any of title/brief/text looks non-zh.
    """
    for k in ("EVENT_TITLE", "EVENT_BRIEF", "EVENT_TEXT"):
        v = doc.get(k)
        if v and looks_non_zh(v):
            return True
    return False
