"""V2 名称的展示与索引归一化。"""

from __future__ import annotations

import re
import unicodedata


_WS_RE = re.compile(r"\s+")


def normalize_name(value: str) -> str:
    """NFKC、小写并移除标点/分隔符/控制符，生成检索键。"""

    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return "".join(
        char for char in text
        if not unicodedata.category(char).startswith(("P", "Z", "C"))
    )


def display_normalize(value: str) -> str:
    """保留可读字符，仅做 NFKC 与空白压缩。"""

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return _WS_RE.sub(" ", text)
