"""配置模型与配置页面之间的显式兼容契约。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from IISConfig import IISSettings


# 修改 IISSettings 后必须审查完整编辑器和首次配置向导，再更新此指纹。
# 运行时页面会报警告，测试也会失败，避免 schema 悄悄领先于界面。
REVIEWED_IIS_SCHEMA_SHA256 = "fecaa194c8dec368a1678d50c2c7fbb503cbc6e8f341ff679c09e2abc3a0b943"


def schema_digest(schema: dict[str, Any] | None = None) -> str:
    value = schema if schema is not None else IISSettings.model_json_schema()
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def schema_ui_contract() -> dict[str, Any]:
    actual = schema_digest()
    compatible = actual == REVIEWED_IIS_SCHEMA_SHA256
    return {
        "compatible": compatible,
        "reviewed_sha256": REVIEWED_IIS_SCHEMA_SHA256,
        "actual_sha256": actual,
        "warning": None if compatible else "schema_ui_review_required",
    }
