# EntityAlias/normalize.py
# 名称归一化：大小写、全半角、空白、标点。用于精确匹配键与模糊匹配输入。

import re
import unicodedata

_WS_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """归一化名称用于匹配：NFKC、去首尾空白、小写、去除全部标点与空白。

    结果作为精确匹配的 key；对 CJK 与拉丁文同样适用。
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name)).strip().lower()
    chars = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("Z") or cat.startswith("C"):
            continue
        chars.append(ch)
    return "".join(chars)


def display_normalize(name: str) -> str:
    """轻度归一化（保留可读性）：NFKC、压缩空白、去首尾。用于存储前的清洗。"""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name)).strip()
    return _WS_RE.sub(" ", s)
