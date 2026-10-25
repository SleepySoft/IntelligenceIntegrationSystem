"""IIS 主程序的声明式 YAML 配置引擎。

外部配置只描述部署差异；完整默认值、字段校验和各组件需要的运行时投影都留在
本模块。AI Client、采集器及各子系统拥有独立配置，不在这里重复声明。
"""

from __future__ import annotations

import logging
import re
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


class BatchAnalysisSettings(_StrictModel):
    """Provider Batch 积压分析（凌晨空闲半价）。默认关闭。"""
    enabled: bool = False
    base_url: str = "https://batch-api-cn.xiaomimimo.com/v1"
    model: str = "mimo-v2.6-flash"
    lookback_days: int = Field(7, ge=1, le=30)
    max_items: int = Field(500, ge=1, le=5000)
    poll_interval_sec: float = Field(300, gt=0)


class HubSettings(_StrictModel):
    workers: int = Field(1, ge=1)
    max_inflight: int = Field(2000, ge=1)
    client_wait_timeout_sec: float = Field(60, gt=0)
    submission_timeout_sec: float = Field(30, gt=0)
    replay_unarchived: bool = True
    conversation_retention_days: int = Field(7, ge=1)
    shadow_versions: list[int] = Field(default_factory=list)
    # 影子链路采样率：1.0 = 每条主链成功样本都跑影子；0.1 = 约 10% 样本跑影子，
    # 用于控制灰度 Token 成本。仅对 shadow_versions 非空时生效。
    shadow_sample_rate: float = Field(1.0, ge=0.0, le=1.0)
    # 影子链路定向 AI 客户端分组（ai_clients.yaml 的 group）；固定后可与主链形成
    # 受控的 同模型比 prompt / 同 prompt 比模型 对比。空 = 随机分配。
    shadow_client_group: str = ""
    # 新鲜度阈值（小时）：超过该年龄的情报进 REPLAY 低优先级车道，防止冷启动/久停后
    # 旧情报淹没问题主队列。0 = 不启用（全部进主队列）。
    max_live_age_hours: float = Field(0, ge=0)
    # 分析年龄硬上限（小时）：超旧情报直接弃用不分析（intake 拒绝 + 回放跳过标 X）。
    # 0 = 不启用。与 max_live_age_hours 构成 新鲜→主队列 / 超龄→慢车道 / 超旧→弃用 三层。
    max_analysis_age_hours: float = Field(0, ge=0)
    # 未处理记录回放顺序：True = 新的优先（冷启动先消化新情报），False = 按入库顺序。
    replay_newest_first: bool = True
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


class TranslationSettings(_StrictModel):
    enabled: bool = True
    client_group: str = "translate_small"

    @field_validator("client_group")
    @classmethod
    def _client_group_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("client_group 不能为空")
        return value.strip()


class SituationSettings(_StrictModel):
    enabled: bool = True
    subsystem: str | None = None
    window_days: int = Field(7, ge=1, le=365)
    min_score: float = Field(6.0, ge=0, le=10)
    geocoding: bool = True
    # 态势看板细节参数（与 SituationViews._dashboard_profile 的界限保持一致）
    min_support: int = Field(2, ge=1, le=50)            # 实体图：关系最少独立事件数
    max_nodes: int = Field(80, ge=10, le=5000)          # 实体图节点上限
    max_edges: int = Field(240, ge=10, le=20000)        # 实体图边上限
    max_articles: int = Field(5000, ge=100, le=20000)   # 单次分析的最大文章数
    primary_only: bool = True                           # 只用主事件构图
    keep_high_score_singletons: bool = True             # 保留高评分单次关系


class ToggleSettings(_StrictModel):
    enabled: bool = True


class IntelligenceViewsSettings(ToggleSettings):
    # /views and its fixed-scope, read-only data dependencies are public by
    # default. Unscoped SituationViews queries remain authenticated.
    public_access: bool = True
    # 公开访问使用更小的固定窗口；空值表示沿用 situation.window_days。
    public_window_days: int | None = Field(None, ge=1, le=365)
    # 登录用户仍可使用时间线自身的 8 实体上限。
    public_timeline_max_entities: int = Field(8, ge=1, le=8)


class EntityAliasWikidataSettings(_StrictModel):
    """Wikidata 在线建议源（本地查不到时的回落）。"""
    enabled: bool = True
    user_agent: str = ""          # 空 = 内置 UA；建议部署时填工具名+联系方式
    proxy: str = ""               # 空 = GlobalConfig.DEFAULT_PROXY
    min_interval_sec: float = Field(0.5, ge=0)
    timeout_sec: float = Field(10.0, gt=0)
    langs: str = "zh,en"          # prefill 拉取的语言集，逗号分隔


class EntityAliasSettings(_StrictModel):
    enabled: bool = True
    url_prefix: str = "/entity-alias"
    data_dir: str = ""            # 空 = 包内 EntityAlias/data
    vector_enabled: bool = True   # 复用 vector 子系统的 VectorDB 做语义召回
    wikidata: EntityAliasWikidataSettings = Field(
        default_factory=EntityAliasWikidataSettings)

    @field_validator("url_prefix")
    @classmethod
    def _valid_prefix(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith("/"):
            raise ValueError("url_prefix 必须以 / 开头")
        return value


class OptionalSubsystemSettings(_StrictModel):
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    vector: VectorSettings = Field(default_factory=VectorSettings)
    situation: SituationSettings = Field(default_factory=SituationSettings)
    intelligence_views: IntelligenceViewsSettings = Field(
        default_factory=IntelligenceViewsSettings)
    entity_alias: EntityAliasSettings = Field(default_factory=EntityAliasSettings)
    batch_analysis: BatchAnalysisSettings = Field(default_factory=BatchAnalysisSettings)


class AccessSettings(_StrictModel):
    rpc_tokens: list[str] = Field(default_factory=list)
    collector_tokens: list[str] = Field(default_factory=list)

    @field_validator("rpc_tokens", "collector_tokens")
    @classmethod
    def _normalize_tokens(cls, values: list[str]) -> list[str]:
        return [str(value).strip() for value in values if str(value).strip()]


class PublicSearchSettings(_StrictModel):
    page_size: int = Field(50, ge=1, le=100)
    max_pages: int = Field(5, ge=1, le=100)
    window_days: int = Field(30, ge=1, le=3650)
    requests_per_minute: int = Field(10, ge=1)
    vector_enabled: bool = False
    vector_page_size: int = Field(10, ge=1, le=100)
    vector_top_n: int = Field(20, ge=1, le=1000)


class PostSettings(_StrictModel):
    watch_enabled: bool = True
    # Trailing-edge quiet period: deliberately longer than common 1s auto-save
    # intervals, so an active editing session does not compile every save.
    debounce_ms: int = Field(2000, ge=50, le=60_000)
    # Continuous edits still publish occasionally instead of starving forever.
    max_wait_ms: int = Field(30_000, ge=50, le=300_000)

    @model_validator(mode="after")
    def _max_wait_covers_debounce(self):
        if self.max_wait_ms < self.debounce_ms:
            raise ValueError("max_wait_ms 必须大于或等于 debounce_ms")
        return self


class I18nSettings(_StrictModel):
    """Web 界面翻译配置；键使用对外 BCP 47 标签，值使用 gettext 目录名。"""

    enabled: bool = True
    default_locale: str = "zh-CN"
    supported_locales: dict[str, str] = Field(default_factory=lambda: {
        "zh-CN": "zh_Hans",
        "en": "en",
    })

    @field_validator("default_locale")
    @classmethod
    def _valid_default_locale(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", value):
            raise ValueError("default_locale 必须是 BCP 47 风格的语言标签")
        return value

    @field_validator("supported_locales")
    @classmethod
    def _valid_supported_locales(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for external_tag, catalog_locale in value.items():
            external_tag = str(external_tag).strip()
            catalog_locale = str(catalog_locale).strip()
            if not re.fullmatch(
                    r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", external_tag):
                raise ValueError(f"无效的界面语言标签: {external_tag!r}")
            if not re.fullmatch(r"[A-Za-z]{2,8}(?:_[A-Za-z0-9]{1,8})*", catalog_locale):
                raise ValueError(f"无效的 gettext 语言目录名: {catalog_locale!r}")
            if external_tag.casefold() in {tag.casefold() for tag in normalized}:
                raise ValueError(f"界面语言标签重复: {external_tag!r}")
            normalized[external_tag] = catalog_locale
        if not normalized:
            raise ValueError("supported_locales 至少需要一种语言")
        return normalized

    @model_validator(mode="after")
    def _default_must_be_supported(self):
        if self.default_locale.casefold() not in {
                tag.casefold() for tag in self.supported_locales}:
            raise ValueError("default_locale 必须出现在 supported_locales 中")
        return self


class WebSettings(_StrictModel):
    i18n: I18nSettings = Field(default_factory=I18nSettings)
    public_search: PublicSearchSettings = Field(default_factory=PublicSearchSettings)
    posts: PostSettings = Field(default_factory=PostSettings)


class IISSettings(_StrictModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    hub: HubSettings = Field(default_factory=HubSettings)
    access: AccessSettings = Field(default_factory=AccessSettings)
    optional_subsystems: OptionalSubsystemSettings = Field(
        default_factory=OptionalSubsystemSettings)
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

        optional_subsystems = settings.optional_subsystems
        translation = optional_subsystems.translation
        vector = optional_subsystems.vector
        situation = optional_subsystems.situation
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
                "max_live_age_hours": settings.hub.max_live_age_hours,
                "max_analysis_age_hours": settings.hub.max_analysis_age_hours,
                "replay_newest_first": settings.hub.replay_newest_first,
                "shadow_analysis": {
                    "enabled": bool(settings.hub.shadow_versions),
                    "versions": settings.hub.shadow_versions,
                    "sample_rate": settings.hub.shadow_sample_rate,
                    "client_group": settings.hub.shadow_client_group,
                    "directory": "_data/analysis_shadow",
                    "max_files": 1000,
                },
                "analysis_failure_dump": {
                    "enabled": True,
                    "directory": "_data/analysis_failures",
                    "max_files": 500,
                },
                "manual_debug": {"max_results": 50, "worker_count": 1},
                "translation": {
                    "enabled": translation.enabled,
                    "client_group": translation.client_group,
                },
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
                "i18n": settings.web.i18n.model_dump(mode="json"),
                "situation_views": {
                    "enabled": situation.enabled,
                    "geocoding": {"enabled": situation.geocoding},
                    "dashboard": {
                        "subsystem": situation_subsystem,
                        "window_days": situation.window_days,
                        "min_score": situation.min_score,
                        "min_support": situation.min_support,
                        "max_nodes": situation.max_nodes,
                        "max_edges": situation.max_edges,
                        "max_articles": situation.max_articles,
                        "primary_only": situation.primary_only,
                        "keep_high_score_singletons": situation.keep_high_score_singletons,
                    },
                },
                "intelligence_views": {
                    "enabled": optional_subsystems.intelligence_views.enabled,
                    "public_access": optional_subsystems.intelligence_views.public_access,
                    "public_window_days": optional_subsystems.intelligence_views.public_window_days,
                    "public_timeline_max_entities": (
                        optional_subsystems.intelligence_views.public_timeline_max_entities),
                    "url_prefix": "/views",
                    "situation_prefix": "/situation",
                    "default_view": "list",
                    "situation": {
                        "subsystem": situation_subsystem,
                        "window_days": situation.window_days,
                        "public_window_days": (
                            optional_subsystems.intelligence_views.public_window_days),
                        "timeline_max_entities": 8,
                        "public_timeline_max_entities": (
                            optional_subsystems.intelligence_views.public_timeline_max_entities),
                        "min_score": situation.min_score,
                        "min_support": situation.min_support,
                        "max_nodes": situation.max_nodes,
                        "max_edges": situation.max_edges,
                        "max_articles": situation.max_articles,
                        "primary_only": situation.primary_only,
                        "keep_high_score_singletons": situation.keep_high_score_singletons,
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
                "posts": {
                    "watch_enabled": settings.web.posts.watch_enabled,
                    "debounce_ms": settings.web.posts.debounce_ms,
                    "max_wait_ms": settings.web.posts.max_wait_ms,
                },
            },
            "monitor": {
                "update_interval_sec": 5.0,
                "thread_stats_interval_sec": 10.0,
            },
            "entity_alias": {
                "enabled": optional_subsystems.entity_alias.enabled,
                "url_prefix": optional_subsystems.entity_alias.url_prefix,
                "data_dir": optional_subsystems.entity_alias.data_dir,
                "vector_enabled": optional_subsystems.entity_alias.vector_enabled,
                "wikidata": {
                    "enabled": optional_subsystems.entity_alias.wikidata.enabled,
                    "user_agent": optional_subsystems.entity_alias.wikidata.user_agent,
                    "proxy": optional_subsystems.entity_alias.wikidata.proxy,
                    "min_interval_sec": optional_subsystems.entity_alias.wikidata.min_interval_sec,
                    "timeout_sec": optional_subsystems.entity_alias.wikidata.timeout_sec,
                    "langs": optional_subsystems.entity_alias.wikidata.langs,
                },
            },
            "batch_analysis": optional_subsystems.batch_analysis.model_dump(),
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
