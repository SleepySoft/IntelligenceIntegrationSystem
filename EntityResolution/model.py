"""实体解析的稳定引用与业务错误。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


_LOCAL_ENTITY_ID = re.compile(r"^ENT[1-9][0-9]*$")


class ResolutionError(Exception):
    """可映射为 Web 状态码的实体解析业务错误。"""

    def __init__(self, message: str, code: str = "bad_request"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class EntityRef:
    """Event V4 文章内实体的稳定引用。"""

    subsystem: str
    intelligence_uuid: str
    local_entity_id: str

    def __post_init__(self) -> None:
        if not self.subsystem.strip():
            raise ResolutionError("subsystem 不能为空")
        if not self.intelligence_uuid.strip():
            raise ResolutionError("intelligence_uuid 不能为空")
        if not _LOCAL_ENTITY_ID.fullmatch(self.local_entity_id.strip()):
            raise ResolutionError("local_entity_id 必须是有效的 ENT ID")

    @property
    def key(self) -> str:
        return f"{self.subsystem.strip()}:{self.intelligence_uuid.strip()}:{self.local_entity_id.strip()}"

    def to_dict(self) -> dict[str, str]:
        return {
            "subsystem": self.subsystem.strip(),
            "intelligence_uuid": self.intelligence_uuid.strip(),
            "local_entity_id": self.local_entity_id.strip(),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EntityRef":
        return cls(
            subsystem=str(value.get("subsystem") or "").strip(),
            intelligence_uuid=str(value.get("intelligence_uuid") or "").strip(),
            local_entity_id=str(value.get("local_entity_id") or "").strip(),
        )
