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


@dataclass(frozen=True, slots=True, init=False)
class EntityRef:
    """Event V4 文章内实体的稳定引用。"""

    domain_name: str
    intelligence_uuid: str
    local_entity_id: str

    def __init__(self, domain_name: str = "", intelligence_uuid: str = "",
                 local_entity_id: str = "", *, subsystem: str = ""):
        if domain_name and subsystem and domain_name != subsystem:
            raise ResolutionError("domain_name 与旧 subsystem 不一致")
        object.__setattr__(self, "domain_name", str(domain_name or subsystem).strip())
        object.__setattr__(self, "intelligence_uuid", str(intelligence_uuid).strip())
        object.__setattr__(self, "local_entity_id", str(local_entity_id).strip())
        self.__post_init__()

    def __post_init__(self) -> None:
        if not self.domain_name:
            raise ResolutionError("domain_name 不能为空")
        if not self.intelligence_uuid.strip():
            raise ResolutionError("intelligence_uuid 不能为空")
        if not _LOCAL_ENTITY_ID.fullmatch(self.local_entity_id.strip()):
            raise ResolutionError("local_entity_id 必须是有效的 ENT ID")

    @property
    def key(self) -> str:
        return f"{self.domain_name}:{self.intelligence_uuid}:{self.local_entity_id}"

    @property
    def subsystem(self) -> str:
        return self.domain_name

    def to_dict(self) -> dict[str, str]:
        return {
            "domain_name": self.domain_name,
            "intelligence_uuid": self.intelligence_uuid.strip(),
            "local_entity_id": self.local_entity_id.strip(),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EntityRef":
        return cls(
            domain_name=str(
                value.get("domain_name") or value.get("domain")
                or value.get("subsystem") or "").strip(),
            intelligence_uuid=str(value.get("intelligence_uuid") or "").strip(),
            local_entity_id=str(value.get("local_entity_id") or "").strip(),
        )
