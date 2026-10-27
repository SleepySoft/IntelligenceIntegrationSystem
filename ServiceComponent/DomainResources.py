# ServiceComponent/DomainResources.py
"""
情报域资源注册表。

每个情报域拥有一套独立资源（Mongo collection、查询引擎、prompt 表、URL 前缀等），
但分析与归档流程完全复用 IntelligenceHub 的同一套框架。

外部配置入口位于 iis.yaml 的 `hub.domains` 段：

    hub:
      domains:
        default: news
        enabled:
          news: 国际新闻
          finance: 财经情报

领域详细配置（collection 前缀、prompt 文件、URL 前缀等）分离到独立文件，
新增领域只需要新增配置目录 + 修改入口列表，无需改动代码。
"""

import os
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from pymongo import ASCENDING

from GlobalConfig import CONFIG_PATH
from Tools.MongoDBAccess import MongoDBStorage
from ServiceComponent.IntelligenceStatisticsEngine import IntelligenceStatisticsEngine
from ServiceComponent.EventV4QueryEngine import EventV4QueryEngine
from ServiceComponent.intelligence_contracts import (
    APPENDIX_RETRY_NEXT_AT,
    CACHE_ARCHIVED_FLAG_FIELD,
    EventV4CollectionSuffix,
    appendix_path,
)
from ServiceComponent.event_v4_repositories import MongoEventV4ArchiveRepository


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# 默认（news）领域沿用既有命名空间前缀。
LEGACY_COLLECTION_PREFIX = 'intelligence_'

# 内置测试领域名：未显式配置时自动登记。测试数据经统一 /collect 流程进入
# （沿用 collector token 鉴权），归档到独立集合（dry_run_intelligence_*），
# 与正式数据完全隔离，可直接清空集合复位。
DRY_RUN_DOMAIN_NAME = 'dry_run'

def _resolve_path(path: str, config_root: Optional[str] = None) -> str:
    """解析相对路径：依次尝试 原样 / 项目 _config 目录 / 项目根目录。"""
    if os.path.isabs(path):
        return path
    candidates = [path]
    if config_root:
        candidates.append(os.path.join(config_root, path))
    candidates.append(os.path.join(CONFIG_PATH, path))
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(project_root, path))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def _resolve_domain_dir(name: str) -> Optional[str]:
    """解析领域配置目录；旧 ``subsystems`` 目录仅作兼容回退。"""
    for base in (CONFIG_PATH, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '_config')):
        for directory_name in ('domains', 'subsystems'):
            candidate = os.path.join(base, directory_name, name)
            if os.path.isdir(candidate):
                return candidate
    return None


def _discover_plugin_files(config_dir) -> List[str]:
    """在领域配置目录中发现 UI 插件主入口：ui_plugin.js。
    其他资源（assets/*.css 等）应由 ui_plugin.js 随需引入，不作为页面自动加载的主入口。"""
    if not config_dir or not os.path.isdir(config_dir):
        return []
    entry = os.path.join(config_dir, 'ui_plugin.js')
    if os.path.isfile(entry):
        return [entry]
    return []

def _load_schema_meta(config_dir: Optional[str]) -> Dict[str, Any]:
    """????? schema.json??????????? {}?"""
    if not config_dir:
        return {}
    sp = os.path.join(config_dir, 'schema.json')
    if not os.path.isfile(sp):
        return {}
    try:
        with open(sp, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Load domain schema failed: {sp} ({e})")
        return {}


@dataclass
class DomainContext:
    """
    一个情报域持有的全部独立资源。
    每个领域有自己的一套数据库访问实例与查询引擎。
    """
    name: str
    display_name: str = ''
    is_default: bool = False
    enabled: bool = True
    url_prefix: str = ''                     # '' 表示挂载在根路径（默认领域）
    collection_prefix: str = LEGACY_COLLECTION_PREFIX
    # 领域实现工厂：内置短名（news/industry）或 ``module:callable``。
    domain_factory: str = 'news'

    mongo_db_cache: Optional[MongoDBStorage] = None
    statistics_engine: Optional[IntelligenceStatisticsEngine] = None

    # Event V4 正式存储端口。V4 使用独立集合，避免与历史文档混合 schema。
    event_v4_archive_repository: Any = None
    event_v4_intelligence_collection: Any = None
    event_v4_intelligence_storage: Optional[MongoDBStorage] = None
    event_v4_low_value_collection: Any = None
    event_v4_event_collection: Any = None
    event_v4_outbox_collection: Any = None
    event_v4_query_engine: Optional[EventV4QueryEngine] = None

    ai_client_group: Optional[str] = None
    scoring_config: Optional[Dict[str, Any]] = None

    # ---------- UI 插件 / schema 元数据（本版仅用于插件加载与验证） ----------
    config_dir: Optional[str] = None              # 领域配置目录（已解析的绝对路径）
    plugin_files: List[str] = field(default_factory=list)   # 插件 JS/CSS 文件（ui_plugin.js 等）
    ui_plugin_enabled: bool = False              # 是否启用 UI 插件（默认领域不加载）
    detail_page: Optional[str] = None           # 自定义详情页 url 模板
    schema_meta: Dict[str, Any] = field(default_factory=dict)  # schema 元数据（本版不做强制）

    # 该领域的独立计数器（与 Hub 全局计数器并存，互不影响）
    stats: Dict[str, int] = field(
        default_factory=lambda: {'archived': 0, 'dropped': 0, 'error': 0})

    def collection_name(self, suffix: str) -> str:
        return f"{self.collection_prefix}{suffix}"


class DomainResourceRegistry:
    """按领域名管理 Mongo、查询与页面路由资源。"""

    def __init__(self, default_name: str = 'news'):
        self.default_name = default_name
        self._domains: Dict[str, DomainContext] = {}

    # ---------------------------------------------------------------- Access

    def add(self, ctx: DomainContext):
        self._domains[ctx.name] = ctx

    def get(self, name: Optional[str]) -> Optional[DomainContext]:
        if not name:
            return None
        return self._domains.get(str(name).strip())

    def resolve(self, name: Optional[str]) -> Optional[DomainContext]:
        """空名字解析为默认领域；未知名字返回 None。"""
        if not name or not str(name).strip():
            return self.default()
        return self.get(name)

    def default(self) -> Optional[DomainContext]:
        return self._domains.get(self.default_name)

    def names(self) -> List[str]:
        return [ctx.name for ctx in self._domains.values() if ctx.enabled]

    def enabled_domains(self) -> List[DomainContext]:
        return [ctx for ctx in self._domains.values() if ctx.enabled]

    def enabled_subsystems(self) -> List[DomainContext]:
        """历史兼容别名。"""
        return self.enabled_domains()

    def describe_domains(self) -> List[Dict[str, Any]]:
        return [{
            'name': ctx.name,
            'display_name': ctx.display_name or ctx.name,
            'is_default': ctx.is_default,
            'url_prefix': ctx.url_prefix,
            'enabled': ctx.enabled,
            # 本次进程生命周期内的领域结果；持久累计结果由 HubApplication
            # 直接从各 V4 集合与 cache 状态汇总。
            'stats': dict(ctx.stats),
        } for ctx in self.enabled_domains()]

    def describe(self) -> List[Dict[str, Any]]:
        """历史兼容别名。"""
        return self.describe_domains()

    # ------------------------------------------------------------- Build

    @classmethod
    def build_from_config(
            cls,
            config,
            *,
            mongodb_params: Dict[str, Any],
            db_name: str = 'IntelligenceIntegrationSystem',
            config_root: Optional[str] = None,
    ) -> 'DomainResourceRegistry':
        """
        从 IISConfig 的运行时投影构建注册表。
        配置段缺失时退化为单一默认领域。
        """
        domains_cfg = config.get('intelligence_hub.domains', None)
        default_name = 'news'
        entries: List[Dict[str, Any]] = []
        if domains_cfg:
            default_name = str(domains_cfg.get('default') or 'news').strip() or 'news'
            entries = list(domains_cfg.get('list') or [])
        if not entries:
            entries = [{'name': default_name, 'display_name': default_name}]

        # 保证默认领域一定在列表中且启用（根路径/未具名数据必须有一个归属）
        default_entry = next(
            (e for e in entries if str(e.get('name') or '').strip() == default_name), None)
        if default_entry is None:
            entries.insert(0, {'name': default_name, 'display_name': default_name})
        elif not default_entry.get('enabled', True):
            logger.warning(f"Default domain '{default_name}' disabled by config, force enabled.")
            default_entry['enabled'] = True

        # 内置 dry_run 测试领域：未显式配置时自动登记（显式配置可覆盖/禁用）
        if not any(str(e.get('name') or '').strip() == DRY_RUN_DOMAIN_NAME for e in entries):
            entries.append({'name': DRY_RUN_DOMAIN_NAME, 'display_name': '干跑测试'})

        registry = cls(default_name=default_name)

        for entry in entries:
            name = str(entry.get('name') or '').strip()
            if not name:
                continue
            enabled = bool(entry.get('enabled', True))
            if not enabled:
                logger.info(f"Domain '{name}' disabled by config, skip.")
                continue

            detail = cls._load_detail(entry, config_root)
            is_default = (name == default_name)
            collection_prefix = str(
                detail.get('collection_prefix')
                or (LEGACY_COLLECTION_PREFIX if is_default else f"{name}_intelligence_"))
            url_prefix = str(detail.get('url_prefix') or ('' if is_default else f"/{name}")).strip()

            # ?????????? UI ?? / schema ???????????????
            config_dir = _resolve_domain_dir(name)
            plugin_files = _discover_plugin_files(config_dir)
            ui_plugin_enabled = bool(detail.get('ui_plugin', True)) and bool(plugin_files)
            detail_page = detail.get('detail_page')
            schema_meta = _load_schema_meta(config_dir)

            ctx = DomainContext(
                name=name,
                display_name=str(detail.get('display_name') or name),
                is_default=is_default,
                enabled=True,
                url_prefix=url_prefix,
                collection_prefix=collection_prefix,
                domain_factory=str(detail.get('factory') or 'news').strip() or 'news',
                ai_client_group=detail.get('ai_client_group'),
                scoring_config=detail.get('scoring'),
                config_dir=config_dir,
                plugin_files=plugin_files,
                ui_plugin_enabled=ui_plugin_enabled,
                detail_page=detail_page,
                schema_meta=schema_meta,
            )

            # -------- 数据库访问实例（每个领域一套，互不干扰） --------
            mongodb_params = dict(mongodb_params or {})
            ctx.mongo_db_cache = MongoDBStorage(
                host=mongodb_params.get('host', 'localhost'),
                port=mongodb_params.get('port', 27017),
                db_name=db_name,
                username=mongodb_params.get('username'),
                password=mongodb_params.get('password'),
                collection_name=ctx.collection_name('cached'),
                indexes=[
                    [
                        (CACHE_ARCHIVED_FLAG_FIELD, ASCENDING),
                        (appendix_path(APPENDIX_RETRY_NEXT_AT), ASCENDING),
                    ],
                    [("collect_time", ASCENDING), ("informant", ASCENDING)],
                ])
            # -------- Event V4 存储 --------
            database = ctx.mongo_db_cache.db
            ctx.event_v4_intelligence_collection = database[
                ctx.collection_name(EventV4CollectionSuffix.ARCHIVED)]
            ctx.event_v4_intelligence_storage = MongoDBStorage.from_collection(
                ctx.event_v4_intelligence_collection)
            ctx.event_v4_low_value_collection = database[
                ctx.collection_name(EventV4CollectionSuffix.LOW_VALUE)]
            ctx.event_v4_event_collection = database[
                ctx.collection_name(EventV4CollectionSuffix.EVENTS)]
            ctx.event_v4_outbox_collection = database[
                ctx.collection_name(EventV4CollectionSuffix.OUTBOX)]

            archive_repository = MongoEventV4ArchiveRepository(
                intelligence_collection=ctx.event_v4_intelligence_collection,
                low_value_collection=ctx.event_v4_low_value_collection,
                event_collection=ctx.event_v4_event_collection,
                outbox_collection=ctx.event_v4_outbox_collection,
            )
            archive_repository.ensure_indexes()
            recovered = archive_repository.recover_pending()
            if recovered:
                logger.warning(
                    "Domain '%s' recovered %s pending Event V4 commits.",
                    name, recovered)
            ctx.event_v4_archive_repository = archive_repository
            ctx.event_v4_query_engine = EventV4QueryEngine(
                ctx.event_v4_intelligence_collection,
                ctx.event_v4_event_collection,
            )
            ctx.statistics_engine = IntelligenceStatisticsEngine(
                ctx.event_v4_intelligence_collection,
                cache_collection=ctx.mongo_db_cache.collection,
                low_value_collection=ctx.event_v4_low_value_collection,
                intake_rejected_collection=database[
                    ctx.collection_name(EventV4CollectionSuffix.INTAKE_REJECTED)],
            )

            registry.add(ctx)
            logger.info(
                f"Domain '{name}' ready: collections={ctx.collection_prefix}*, "
                f"url_prefix='{url_prefix}', factory={ctx.domain_factory!r}")

        return registry

    @staticmethod
    def _load_detail(entry: Dict[str, Any], config_root: Optional[str]) -> Dict[str, Any]:
        """领域入口 + 分离的详细配置文件合并（入口字段优先）。"""
        detail = dict(entry)
        config_file = entry.get('config_file')
        if config_file:
            path = _resolve_path(str(config_file), config_root)
            if os.path.isfile(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        detail.update(json.load(f))
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning(f"Load domain config file failed: {path} ({e})")
            else:
                logger.warning(f"Domain config file not found: {path}")
        return detail
