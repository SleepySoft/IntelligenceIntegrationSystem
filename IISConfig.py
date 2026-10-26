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
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


logger = logging.getLogger(__name__)


def iis_msg(message: str) -> str:
    """标记可进入 gettext 目录、同时保留在 JSON Schema 中的配置说明。"""

    return message


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(_StrictModel):
    host: str = Field("0.0.0.0", description=iis_msg(
        "Network address on which the IIS web service listens. Use 0.0.0.0 to accept connections on all interfaces."))
    port: int = Field(5000, ge=1, le=65535, description=iis_msg(
        "TCP port exposed by the IIS web service."))
    threads: int = Field(4, ge=1, description=iis_msg(
        "Number of request threads used by a threaded WSGI server such as Waitress."))
    workers: int = Field(1, ge=1, description=iis_msg(
        "Number of WSGI worker processes when the selected server supports multiple workers."))
    public_origin: str | None = Field(None, description=iis_msg(
        "Public base URL used to generate external links, for example https://intel.example.com. Leave empty to use the local service address."))

    @field_validator("host")
    @classmethod
    def _host_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("host 不能为空")
        return value.strip()

    def origin(self) -> str:
        return (self.public_origin or f"http://127.0.0.1:{self.port}").rstrip("/")


class DatabaseSettings(_StrictModel):
    host: str = Field("localhost", description=iis_msg(
        "Hostname or IP address of the external MongoDB service."))
    port: int = Field(27017, ge=1, le=65535, description=iis_msg(
        "TCP port of the external MongoDB service."))
    username: str = Field("", description=iis_msg(
        "MongoDB username. Leave empty when MongoDB authentication is disabled."))
    password: str = Field("", description=iis_msg(
        "MongoDB password. The configuration page masks an existing value."))


class DomainSettings(_StrictModel):
    default: str = Field("news", description=iis_msg(
        "Name of the intelligence domain used when a URL does not include a domain prefix."))
    enabled: dict[str, str] = Field(
        default_factory=lambda: {"news": "国际新闻"}, description=iis_msg(
            "Mapping of enabled domain identifiers to their display names. The default domain must be present."))

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
            raise ValueError("enabled 至少需要一个情报域")
        return normalized


class BatchAnalysisSettings(_StrictModel):
    """Provider Batch 积压分析（凌晨空闲半价）。默认关闭。"""
    enabled: bool = Field(False, description=iis_msg(
        "Enable asynchronous provider batch analysis for eligible backlog items."))
    base_url: str = Field("https://batch-api-cn.xiaomimimo.com/v1", description=iis_msg(
        "Base URL of the provider Batch API."))
    model: str = Field("mimo-v2.6-flash", description=iis_msg(
        "Provider model identifier used for batch analysis."))
    lookback_days: int = Field(7, ge=1, le=30, description=iis_msg(
        "Maximum age in days of backlog items eligible for a batch job."))
    max_items: int = Field(500, ge=1, le=5000, description=iis_msg(
        "Maximum number of items submitted in one batch job."))
    poll_interval_sec: float = Field(300, gt=0, description=iis_msg(
        "Seconds between checks for provider batch job completion."))


class HubSettings(_StrictModel):
    workers: int = Field(1, ge=1, description=iis_msg(
        "Number of concurrent AI analysis workers. Keep this no higher than available AI clients unless intentional."))
    max_inflight: int = Field(2000, ge=1, description=iis_msg(
        "Maximum accepted but unfinished intelligence items kept in the runtime pipeline."))
    client_wait_timeout_sec: float = Field(60, gt=0, description=iis_msg(
        "Maximum seconds an analysis worker waits for an available AI client."))
    submission_timeout_sec: float = Field(30, gt=0, description=iis_msg(
        "Maximum seconds the collection endpoint waits for intake and archive-stage acknowledgement."))
    replay_unarchived: bool = Field(True, description=iis_msg(
        "Automatically replay unfinished cached items when IIS starts. Disable for manual replay."))
    conversation_retention_days: int = Field(7, ge=1, description=iis_msg(
        "Number of days to retain stored AI conversation diagnostics."))
    shadow_versions: list[int] = Field(default_factory=list, description=iis_msg(
        "Prompt versions run as shadow analysis after the main result succeeds. Shadow results are not archived."))
    # 影子链路采样率：1.0 = 每条主链成功样本都跑影子；0.1 = 约 10% 样本跑影子，
    # 用于控制灰度 Token 成本。仅对 shadow_versions 非空时生效。
    shadow_sample_rate: float = Field(1.0, ge=0.0, le=1.0, description=iis_msg(
        "Fraction of successful main analyses selected for all configured shadow versions, from 0 to 1."))
    # 影子链路定向 AI 客户端分组（ai_clients.yaml 的 group）；固定后可与主链形成
    # 受控的 同模型比 prompt / 同 prompt 比模型 对比。空 = 随机分配。
    shadow_client_group: str = Field("", description=iis_msg(
        "AI client group reserved for shadow analysis. Leave empty for normal client selection."))
    # 新鲜度阈值（小时）：超过该年龄的情报进 REPLAY 低优先级车道，防止冷启动/久停后
    # 旧情报淹没问题主队列。0 = 不启用（全部进主队列）。
    max_live_age_hours: float = Field(0, ge=0, description=iis_msg(
        "Items older than this many hours enter the lower-priority replay lane. Zero disables age-based routing."))
    # 分析年龄硬上限（小时）：超旧情报直接弃用不分析（intake 拒绝 + 回放跳过标 X）。
    # 0 = 不启用。与 max_live_age_hours 构成 新鲜→主队列 / 超龄→慢车道 / 超旧→弃用 三层。
    max_analysis_age_hours: float = Field(0, ge=0, description=iis_msg(
        "Items older than this many hours are discarded without analysis. Zero disables the hard age limit."))
    # 未处理记录回放顺序：True = 新的优先（冷启动先消化新情报），False = 按入库顺序。
    replay_newest_first: bool = Field(True, description=iis_msg(
        "Process newer unfinished cached items before older ones during replay."))
    domains: DomainSettings = Field(
        default_factory=DomainSettings,
        validation_alias=AliasChoices("domains", "subsystems"),
        description=iis_msg("Defines the enabled intelligence domains and the default domain."),
    )


class VectorSettings(_StrictModel):
    enabled: bool = Field(False, description=iis_msg(
        "Enable vector indexing, semantic search, aggregation, and graph features that depend on VectorDB."))
    endpoint: str = Field("http://127.0.0.1:8001", description=iis_msg(
        "HTTP endpoint of the external VectorDB service."))
    database: str = Field("vectordb", description=iis_msg(
        "Logical VectorDB database or collection namespace used by IIS."))
    model: str = Field("BAAI/bge-m3", description=iis_msg(
        "Embedding model identifier expected by the VectorDB service."))

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
    enabled: bool = Field(True, description=iis_msg(
        "Enable asynchronous translation for intelligence that requires a Chinese revision."))
    client_group: str = Field("translate_small", description=iis_msg(
        "AI client group from ai_clients.yaml used for translation jobs."))

    @field_validator("client_group")
    @classmethod
    def _client_group_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("client_group 不能为空")
        return value.strip()


class SituationSettings(_StrictModel):
    enabled: bool = Field(True, description=iis_msg(
        "Enable geographic, entity relationship, and timeline situation views."))
    subsystem: str | None = Field(None, description=iis_msg(
        "Intelligence subsystem used by situation views. Leave empty to use the default subsystem."))
    window_days: int = Field(7, ge=1, le=365, description=iis_msg(
        "Number of recent days included in authenticated situation views."))
    min_score: float = Field(6.0, ge=0, le=10, description=iis_msg(
        "Minimum intelligence score included in situation calculations."))
    geocoding: bool = Field(True, description=iis_msg(
        "Enable location geocoding required by geographic views."))
    # 态势看板细节参数（与 SituationViews._dashboard_profile 的界限保持一致）
    min_support: int = Field(2, ge=1, le=50, description=iis_msg(
        "Minimum number of independent events required to display an entity relationship."))
    max_nodes: int = Field(80, ge=10, le=5000, description=iis_msg(
        "Maximum entity nodes returned by one relationship graph calculation."))
    max_edges: int = Field(240, ge=10, le=20000, description=iis_msg(
        "Maximum relationship edges returned by one graph calculation."))
    max_articles: int = Field(5000, ge=100, le=20000, description=iis_msg(
        "Maximum intelligence articles scanned by one situation calculation."))
    primary_only: bool = Field(True, description=iis_msg(
        "Build relationship graphs from primary events only."))
    keep_high_score_singletons: bool = Field(True, description=iis_msg(
        "Keep high-scoring relationships even when they have only one supporting event."))


class ToggleSettings(_StrictModel):
    enabled: bool = Field(True, description=iis_msg(
        "Enable this optional subsystem."))


class IntelligenceViewsSettings(ToggleSettings):
    # /views and its fixed-scope, read-only data dependencies are public by
    # default. Unscoped SituationViews queries remain authenticated.
    public_access: bool = Field(True, description=iis_msg(
        "Allow unauthenticated access to the fixed-scope read-only /views interface."))
    # 公开访问使用更小的固定窗口；空值表示沿用 situation.window_days。
    public_window_days: int | None = Field(None, ge=1, le=365, description=iis_msg(
        "Recent-day window used for public situation views. Leave empty to use the authenticated window."))
    # 登录用户仍可使用时间线自身的 8 实体上限。
    public_timeline_max_entities: int = Field(8, ge=1, le=8, description=iis_msg(
        "Maximum number of entities accepted by a public timeline request."))


class EntityAliasWikidataSettings(_StrictModel):
    """Wikidata 在线建议源（本地查不到时的回落）。"""
    enabled: bool = Field(True, description=iis_msg(
        "Enable Wikidata suggestions when an entity name is not found locally."))
    user_agent: str = Field("", description=iis_msg(
        "Descriptive HTTP User-Agent for Wikidata requests. Leave empty to use the built-in value."))
    proxy: str = Field("", description=iis_msg(
        "Proxy URL for Wikidata requests. Leave empty to use the project default proxy."))
    min_interval_sec: float = Field(0.5, ge=0, description=iis_msg(
        "Minimum delay in seconds between Wikidata requests."))
    timeout_sec: float = Field(10.0, gt=0, description=iis_msg(
        "Timeout in seconds for one Wikidata request."))
    langs: str = Field("zh,en", description=iis_msg(
        "Comma-separated language codes fetched when prefilling entity names."))


class EntityAliasSettings(_StrictModel):
    enabled: bool = Field(True, description=iis_msg(
        "Enable entity alias resolution and its management interface."))
    url_prefix: str = Field("/entity-alias", description=iis_msg(
        "URL prefix used to mount the entity alias subsystem."))
    data_dir: str = Field("", description=iis_msg(
        "Directory containing entity alias JSONL data. Leave empty to use EntityAlias/data."))
    vector_enabled: bool = Field(True, description=iis_msg(
        "Use the VectorDB service for semantic recall of similar entity names."))
    wikidata: EntityAliasWikidataSettings = Field(
        default_factory=EntityAliasWikidataSettings, description=iis_msg(
            "Settings for the optional Wikidata suggestion source."))

    @field_validator("url_prefix")
    @classmethod
    def _valid_prefix(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith("/"):
            raise ValueError("url_prefix 必须以 / 开头")
        return value


class OptionalSubsystemSettings(_StrictModel):
    translation: TranslationSettings = Field(default_factory=TranslationSettings, description=iis_msg(
        "Asynchronous intelligence translation settings."))
    vector: VectorSettings = Field(default_factory=VectorSettings, description=iis_msg(
        "Vector intelligence integration settings."))
    situation: SituationSettings = Field(default_factory=SituationSettings, description=iis_msg(
        "Situation view and graph calculation settings."))
    intelligence_views: IntelligenceViewsSettings = Field(
        default_factory=IntelligenceViewsSettings, description=iis_msg(
            "Unified intelligence view shell and public access settings."))
    entity_alias: EntityAliasSettings = Field(default_factory=EntityAliasSettings, description=iis_msg(
        "Entity alias resolution settings."))
    batch_analysis: BatchAnalysisSettings = Field(default_factory=BatchAnalysisSettings, description=iis_msg(
        "Provider Batch API backlog analysis settings."))


class AccessSettings(_StrictModel):
    rpc_tokens: list[str] = Field(default_factory=list, description=iis_msg(
        "Tokens accepted by the privileged RPC API. Use independent random values."))
    collector_tokens: list[str] = Field(default_factory=list, description=iis_msg(
        "Tokens accepted by the /collect endpoint. Each crawler must use one configured value."))

    @field_validator("rpc_tokens", "collector_tokens")
    @classmethod
    def _normalize_tokens(cls, values: list[str]) -> list[str]:
        return [str(value).strip() for value in values if str(value).strip()]


class PublicSearchSettings(_StrictModel):
    page_size: int = Field(50, ge=1, le=100, description=iis_msg(
        "Maximum records returned per public MongoDB search page."))
    max_pages: int = Field(5, ge=1, le=100, description=iis_msg(
        "Maximum page number available to unauthenticated search users."))
    window_days: int = Field(30, ge=1, le=3650, description=iis_msg(
        "Maximum content-time window in days available to public search."))
    requests_per_minute: int = Field(10, ge=1, description=iis_msg(
        "Per-client public search request limit per minute."))
    vector_enabled: bool = Field(False, description=iis_msg(
        "Allow unauthenticated users to run vector search."))
    vector_page_size: int = Field(10, ge=1, le=100, description=iis_msg(
        "Maximum records returned per public vector search page."))
    vector_top_n: int = Field(20, ge=1, le=1000, description=iis_msg(
        "Maximum vector candidates recalled before public result pagination."))


class PostSettings(_StrictModel):
    watch_enabled: bool = Field(True, description=iis_msg(
        "Watch repository Markdown posts and publish changes without restarting IIS."))
    # Trailing-edge quiet period: deliberately longer than common 1s auto-save
    # intervals, so an active editing session does not compile every save.
    debounce_ms: int = Field(2000, ge=50, le=60_000, description=iis_msg(
        "Quiet period in milliseconds before recompiling a changed Markdown post."))
    # Continuous edits still publish occasionally instead of starving forever.
    max_wait_ms: int = Field(30_000, ge=50, le=300_000, description=iis_msg(
        "Maximum milliseconds to postpone publication while edits continue."))

    @model_validator(mode="after")
    def _max_wait_covers_debounce(self):
        if self.max_wait_ms < self.debounce_ms:
            raise ValueError("max_wait_ms 必须大于或等于 debounce_ms")
        return self


class I18nSettings(_StrictModel):
    """Web 界面翻译配置；键使用对外 BCP 47 标签，值使用 gettext 目录名。"""

    enabled: bool = Field(True, description=iis_msg(
        "Enable interface language selection from URL, session, and browser preferences."))
    default_locale: str = Field("zh-CN", description=iis_msg(
        "Default BCP 47 interface language used when no valid preference is available."))
    supported_locales: dict[str, str] = Field(default_factory=lambda: {
        "zh-CN": "zh_Hans",
        "en": "en",
    }, description=iis_msg(
        "Mapping of public BCP 47 language tags to gettext catalog directory names."))

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
    i18n: I18nSettings = Field(default_factory=I18nSettings, description=iis_msg(
        "Web interface internationalization settings."))
    public_search: PublicSearchSettings = Field(default_factory=PublicSearchSettings, description=iis_msg(
        "Limits applied to unauthenticated intelligence search."))
    posts: PostSettings = Field(default_factory=PostSettings, description=iis_msg(
        "Markdown post hot-reload settings."))


class IISSettings(_StrictModel):
    server: ServerSettings = Field(default_factory=ServerSettings, description=iis_msg(
        "IIS web server listener and public address settings."))
    database: DatabaseSettings = Field(default_factory=DatabaseSettings, description=iis_msg(
        "Connection settings for the external MongoDB service."))
    hub: HubSettings = Field(default_factory=HubSettings, description=iis_msg(
        "Core intelligence processing pipeline settings."))
    access: AccessSettings = Field(default_factory=AccessSettings, description=iis_msg(
        "Machine-to-machine access tokens."))
    optional_subsystems: OptionalSubsystemSettings = Field(
        default_factory=OptionalSubsystemSettings, description=iis_msg(
            "Settings for independently switchable optional subsystems."))
    web: WebSettings = Field(default_factory=WebSettings, description=iis_msg(
        "Web interface behavior and public access limits."))


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
        domain_entries = []
        for name, display_name in settings.hub.domains.enabled.items():
            entry: dict[str, Any] = {
                "name": name,
                "display_name": display_name,
                "enabled": True,
            }
            detail_file = config_dir / "subsystems" / f"{name}.json"
            if detail_file.is_file():
                entry["config_file"] = str(detail_file)
            domain_entries.append(entry)
        default_name = settings.hub.domains.default
        if default_name not in settings.hub.domains.enabled:
            raise ValueError(
                f"默认情报域 {default_name!r} 不在 hub.domains.enabled 中。")

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
                "domains": {
                    "default": default_name,
                    "list": domain_entries,
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
