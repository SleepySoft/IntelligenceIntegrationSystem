"""所有情报领域共用的采集输入契约。"""

import datetime
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ServiceComponent.intelligence_time import normalize_intelligence_datetime


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class CollectedData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    UUID: str = Field(..., min_length=1)
    token: str = Field(..., min_length=1)
    source: str | None = None
    target: str | None = None
    prompt: str | None = None
    title: str = Field(..., min_length=1)
    authors: list[str] = Field(default_factory=list)
    content: str = Field(..., min_length=10)
    pub_time: Any | None = None
    collect_time: datetime.datetime | None = Field(default_factory=_utc_now)
    informant: str = Field(..., min_length=1)
    subsystem: str = ""
    temp_data: dict = Field(default_factory=dict)

    @field_validator("pub_time", mode="before")
    @classmethod
    def convert_any_time_format(cls, value):
        if value is None or isinstance(value, datetime.datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.datetime.fromtimestamp(value)
        if isinstance(value, time.struct_time):
            return datetime.datetime.fromtimestamp(time.mktime(value))
        if isinstance(value, str):
            try:
                return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S"):
                    try:
                        return datetime.datetime.strptime(value, fmt)
                    except ValueError:
                        continue
        return value

    @field_validator("collect_time", mode="before")
    @classmethod
    def normalize_collect_time(cls, value):
        if value is None:
            return None
        return normalize_intelligence_datetime(value) or value

    @field_validator("informant", mode="before")
    @classmethod
    def normalize_informant(cls, value):
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("informant/source URL cannot be empty")
        return normalized
