"""IIS 主程序的声明式 YAML 配置引擎。

外部配置只描述部署差异；完整默认值、字段校验和各组件需要的运行时投影都留在
本模块。AI Client、采集器及各子系统拥有独立配置，不在这里重复声明。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


logger = logging.getLogger(__name__)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(_StrictModel):
    host: str = "0.0.0.0"
    port: int = Field(5000, ge=1, le=65535)
    threads: int = Field(4, ge=1)
    workers: int = Field(1, ge=1)
    public_origin: str | None = None

    @field_validator("host")
    @classmethod
    def _host_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("host 不能为空")
        return value.strip()

    def origin(self) -> str:
        return (self.public_origin or f"http://127.0.0.1:{self.port}").rstrip("/")


class DatabaseSettings(_StrictModel):
    host: str = "localhost"
    port: int = Field(27017, ge=1, le=65535)
    username: str = ""
    password: str = ""


class SubsystemSettings(_StrictModel):
    default: str = "news"
    enabled: dict[str, str] = Field(
        default_factory=lambda: {"news": "国际新闻"})

    @field_validator("default")
    @classmethod
    def _default_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("default 不能为空")
        return value.strip()

    @field_validator("enabled")
    @classmethod
    def _enabled_not_empty(cls, value: dict[str, str]) -> dict[str, str]:
        normalized = {
            str(name).strip(): str(label or name).strip()
            for name, label in value.items()
            if str(name).strip()
        }
        if not normalized:
            raise ValueError("enabled 至少需要一个子系统")
        return normalized


class HubSettings(_StrictModel):
    workers: int = Field(1, ge=1)
    max_inflight: int = Field(2000, ge=1)
    client_wait_timeout_sec: float = Field(60, gt=0)
    submission_timeout_sec: float = Field(30, gt=0)
    replay_unarchived: bool = True
    conversation_retention_days: int = Field(7, ge=1)
    shadow_versions: list[int] = Field(default_factory=list)
    subsystems: SubsystemSettings = Field(default_factory=SubsystemSettings)


class VectorSettings(_StrictModel):
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:8001"
    database: str = "vectordb"
    model: str = "BAAI/bge-m3"

    @field_validator("endpoint")
    @classmethod
    def _valid_endpoint(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("endpoint 必须是有效的 http(s) URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _enabled_requires_storage(self):
        if self.enabled and (not self.database.strip() or not self.model.strip()):
            raise ValueError("启用 vector 时 database 和 model 不能为空")
        return self


class SituationSettings(_StrictModel):
    enabled: bool = True
    subsystem: str | None = None
    window_days: int = Field(7, ge=1, le=365)
    min_score: float = Field(6.0, ge=0, le=10)
    geocoding: bool = True


class FeatureSettings(_StrictModel):
    translation: bool = True
    vector: VectorSettings = Field(default_factory=VectorSettings)
    situation: SituationSettings = Field(default_factory=SituationSettings)
    intelligence_views: bool = True


class AccessSettings(_StrictModel):
    rpc_tokens: list[str] = Field(default_factory=list)
    collector_tokens: list[str] = Field(default_factory=list)

    @field_validator("rpc_tokens", "collector_tokens")
    @classmethod
    def _normalize_tokens(cls, values: list[str]) -> list[str]:
        return [str(value).strip() for value in values if str(value).strip()]


class PublicSearchSettings(_StrictModel):
    page_size: int = Field(20, ge=1, le=100)
    max_pages: int = Field(5, ge=1, le=100)
    window_days: int = Field(30, ge=1, le=3650)
    requests_per_minute: int = Field(10, ge=1)
    vector_enabled: bool = False
    vector_page_size: int = Field(10, ge=1, le=100)
    vector_top_n: int = Field(20, ge=1, le=1000)


class WebSettings(_StrictModel):
    public_search: PublicSearchSettings = Field(default_factory=PublicSearchSettings)


class IISSettings(_StrictModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    hub: HubSettings = Field(default_factory=HubSettings)
    access: AccessSettings = Field(default_factory=AccessSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    web: WebSettings = Field(default_factory=WebSettings)


class IISConfig:
    """经 schema 校验，并投影为各既有组件可读取的运行时配置。"""

    def __init__(self, settings: IISSettings, *, config_file: Path):
        self.settings = settings
        self.config_file = str(config_file)
        self.config_data = self._runtime_projection(config_file.parent)

    def get(self, key: str, default: Any = None) -> Any:
        value: Any = self.config_data
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def dump_text(self) -> str:
        """输出可安全记录的生效配置，不记录密码和访问 Token。"""

        values = self.settings.model_dump(mode="json")
        values["database"]["password"] = "***" if values["database"]["password"] else ""
        for key in ("rpc_tokens", "collector_tokens"):
            count = len(values["access"][key])
            values["access"][key] = [f"*** ({count} configured)"] if count else []
        return yaml.safe_dump(values, allow_unicode=True, sort_keys=False).rstrip()

    def _runtime_projection(self, config_dir: Path) -> dict[str, Any]:
        settings = self.settings
        subsystem_entries = []
        for name, display_name in settings.hub.subsystems.enabled.items():
            entry: dict[str, Any] = {
                "name": name,
                "display_name": display_name,
                "enabled": True,
            }
            detail_file = config_dir / "subsystems" / f"{name}.json"
            if detail_file.is_file():
                entry["config_file"] = str(detail_file)
            subsystem_entries.append(entry)
        default_name = settings.hub.subsystems.default
        if default_name not in settings.hub.subsystems.enabled:
            raise ValueError(
                f"默认子系统 {default_name!r} 不在 hub.subsystems.enabled 中。")

        vector = settings.features.vector
        situation = settings.features.situation
        situation_subsystem = situation.subsystem or default_name
        search = settings.web.public_search
        origin = settings.server.origin()
        return {
            "mongodb": {
                "host": settings.database.host,
                "port": settings.database.port,
                "user": settings.database.username,
                "password": settings.database.password,
            },
            "intelligence_hub": {
                "ai_analysis_thread": settings.hub.workers,
                "ai_client_wait_timeout_sec": settings.hub.client_wait_timeout_sec,
                "max_inflight": settings.hub.max_inflight,
                "submission_ack_timeout_sec": settings.hub.submission_timeout_sec,
                "replay_unarchived": settings.hub.replay_unarchived,
                "conversation_retention_days": settings.hub.conversation_retention_days,
                "shadow_analysis": {
                    "enabled": bool(settings.hub.shadow_versions),
                    "versions": settings.hub.shadow_versions,
                    "directory": "_data/analysis_shadow",
                    "max_files": 1000,
                },
                "analysis_failure_dump": {
                    "enabled": True,
                    "directory": "_data/analysis_failures",
                    "max_files": 500,
                },
                "manual_debug": {"max_results": 50, "worker_count": 1},
                "translation": {"enabled": settings.features.translation},
                "subsystems": {
                    "default": default_name,
                    "list": subsystem_entries,
                },
                "systemd_watchdog": {"enabled": True, "interval_sec": 10},
                "vectordb": {
                    "enabled": vector.enabled,
                    "url": vector.endpoint,
                    "vector_db_path": vector.database,
                    "embedding_model_name": vector.model,
                },
            },
            "intelligence_hub_web_service": {
                "service": {"host_url": origin},
                "rss": {"host_prefix": origin},
                "rpc_api": {"tokens": settings.access.rpc_tokens},
                "collector": {"tokens": settings.access.collector_tokens},
                "situation_views": {
                    "enabled": situation.enabled,
                    "geocoding": {"enabled": situation.geocoding},
                    "dashboard": {
                        "subsystem": situation_subsystem,
                        "window_days": situation.window_days,
                        "min_score": situation.min_score,
                    },
                },
                "intelligence_views": {
                    "enabled": settings.features.intelligence_views,
                    "url_prefix": "/views",
                    "situation_prefix": "/situation",
                    "default_view": "list",
                    "situation": {
                        "subsystem": situation_subsystem,
                        "window_days": situation.window_days,
                        "min_score": situation.min_score,
                    },
                },
                "public_search": {
                    "mongo_max_per_page": search.page_size,
                    "mongo_max_page": search.max_pages,
                    "mongo_default_window_days": search.window_days,
                    "vector_max_per_page": search.vector_page_size,
                    "vector_max_top_n": search.vector_top_n,
                    "vector_max_page": 2,
                    "vector_allow_fulltext": search.vector_enabled,
                    "vector_allow_similar": search.vector_enabled,
                    "vector_min_score_threshold": 0.6,
                    "vector_default_window_days": search.window_days,
                    "requests_per_minute": search.requests_per_minute,
                    "max_concurrent_vector": 5,
                },
            },
            "monitor": {
                "update_interval_sec": 5.0,
                "thread_stats_interval_sec": 10.0,
            },
            "ai_client_center": {
                "sqlite_db_file": "ai_client_state_log.db",
                "heartbeat_interval_sec": 30,
                "heartbeat_grace_sec": 120,
                "call_observation": {
                    "enabled": True,
                    "sqlite_db_file": "ai_client_observations.db",
                    "retention_days": 90,
                },
            },
        }


def load_iis_config(config_file, *, fallback_file=None) -> IISConfig:
    """读取并严格校验 IIS YAML；主文件不存在时可加载示例配置。"""

    path = Path(config_file).expanduser().resolve()
    if not path.is_file():
        fallback = Path(fallback_file).expanduser().resolve() if fallback_file else None
        if fallback is None or not fallback.is_file():
            raise ValueError(f"IIS 配置文件不存在: {path}")
        logger.warning("IIS 配置 %s 不存在，加载示例配置 %s。", path, fallback)
        path = fallback
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("IIS 主配置仅支持 .yaml/.yml 文件。")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"IIS YAML 读取失败: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("IIS YAML 顶层必须是对象。")
    settings = IISSettings.model_validate(raw)
    return IISConfig(settings, config_file=path)
