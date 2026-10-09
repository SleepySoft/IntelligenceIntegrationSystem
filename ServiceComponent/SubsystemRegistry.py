# ServiceComponent/SubsystemRegistry.py
"""
多子系统注册表。

每个子系统拥有一套独立资源（Mongo collection、查询引擎、prompt 表、URL 前缀等），
但分析与归档流程完全复用 IntelligenceHub 的同一套框架。

配置入口位于 config.json 的 `intelligence_hub.subsystems` 段：

    "subsystems": {
        "default": "news",
        "list": [
            {"name": "news", "display_name": "国际新闻", "enabled": true},
            {"name": "finance", "display_name": "财经情报", "enabled": true,
             "config_file": "_config/subsystems/finance.json"}
        ]
    }

子系统详细配置（collection 前缀、prompt 文件、URL 前缀等）分离到独立文件，
新增子系统只需要新增配置目录 + 修改入口列表，无需改动代码。
"""

import os
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_TABLE
from GlobalConfig import CONFIG_PATH
from Tools.MongoDBAccess import MongoDBStorage
from ServiceComponent.IntelligenceStatisticsEngine import IntelligenceStatisticsEngine
from ServiceComponent.EventV4QueryEngine import EventV4QueryEngine
from ServiceComponent.event_v4_conversion import DeterministicEntityResolver
from ServiceComponent.event_v4_repositories import (
    MongoEntityRepository,
    MongoEventV4ArchiveRepository,
)


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# 默认（news）子系统沿用既有命名空间前缀。
LEGACY_COLLECTION_PREFIX = 'intelligence_'

# 内置测试子系统名：未显式配置时自动登记。测试数据经统一 /collect 流程进入
# （沿用 collector token 鉴权），归档到独立集合（dry_run_intelligence_*），
# 与正式数据完全隔离，可直接清空集合复位。
DRY_RUN_SUBSYSTEM_NAME = 'dry_run'

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


def _resolve_subsystem_dir(name: str) -> Optional[str]:
    """??????????_config/subsystems/{name}???????????? None?"""
    for base in (CONFIG_PATH, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '_config')):
        candidate = os.path.join(base, 'subsystems', name)
        if os.path.isdir(candidate):
            return candidate
    return None


def _discover_plugin_files(config_dir) -> List[str]:
    """在子系统配置目录中发现 UI 插件主入口：ui_plugin.js。
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
        logger.warning(f"Load subsystem schema failed: {sp} ({e})")
        return {}


@dataclass
class SubsystemContext:
    """
    一个子系统持有的全部独立资源。
    “互不管对方”：每个子系统有自己的一套数据库访问实例与查询引擎。
    """
    name: str
    display_name: str = ''
    is_default: bool = False
    enabled: bool = True
    url_prefix: str = ''                     # '' 表示挂载在根路径（默认子系统）
    collection_prefix: str = LEGACY_COLLECTION_PREFIX

    mongo_db_cache: Optional[MongoDBStorage] = None
    statistics_engine: Optional[IntelligenceStatisticsEngine] = None

    # Event V4 正式存储端口。V4 使用独立集合，避免与历史文档混合 schema。
    event_v4_archive_repository: Any = None
    event_v4_entity_resolver: Any = None
    event_v4_intelligence_collection: Any = None
    event_v4_intelligence_storage: Optional[MongoDBStorage] = None
    event_v4_low_value_collection: Any = None
    event_v4_event_collection: Any = None
    event_v4_entity_collection: Any = None
    event_v4_outbox_collection: Any = None
    event_v4_query_engine: Optional[EventV4QueryEngine] = None

    prompt_table: Dict[int, str] = field(default_factory=dict)   # version -> prompt text

    ai_client_group: Optional[str] = None
    scoring_config: Optional[Dict[str, Any]] = None

    # ---------- UI 插件 / schema 元数据（本版仅用于插件加载与验证） ----------
    config_dir: Optional[str] = None              # 子系统配置目录（已解析的绝对路径）
    plugin_files: List[str] = field(default_factory=list)   # 插件 JS/CSS 文件（ui_plugin.js 等）
    ui_plugin_enabled: bool = False              # 是否启用 UI 插件（默认子系统不加载）
    detail_page: Optional[str] = None           # 自定义详情页 url 模板
    schema_meta: Dict[str, Any] = field(default_factory=dict)  # schema 元数据（本版不做强制）

    # 该子系统的独立计数器（与 Hub 全局计数器并存，互不影响）
    stats: Dict[str, int] = field(
        default_factory=lambda: {'archived': 0, 'dropped': 0, 'error': 0})

    def collection_name(self, suffix: str) -> str:
        return f"{self.collection_prefix}{suffix}"


class SubsystemRegistry:
    """按名字管理所有子系统的上下文。"""

    def __init__(self, default_name: str = 'news'):
        self.default_name = default_name
        self._subsystems: Dict[str, SubsystemContext] = {}

    # ---------------------------------------------------------------- Access

    def add(self, ctx: SubsystemContext):
        self._subsystems[ctx.name] = ctx

    def get(self, name: Optional[str]) -> Optional[SubsystemContext]:
        if not name:
            return None
        return self._subsystems.get(str(name).strip())

    def resolve(self, name: Optional[str]) -> Optional[SubsystemContext]:
        """空名字解析为默认子系统；未知名字返回 None（由调用方决定拒绝）。"""
        if not name or not str(name).strip():
            return self.default()
        return self.get(name)

    def default(self) -> Optional[SubsystemContext]:
        return self._subsystems.get(self.default_name)

    def names(self) -> List[str]:
        return [ctx.name for ctx in self._subsystems.values() if ctx.enabled]

    def enabled_subsystems(self) -> List[SubsystemContext]:
        return [ctx for ctx in self._subsystems.values() if ctx.enabled]

    def describe(self) -> List[Dict[str, Any]]:
        return [{
            'name': ctx.name,
            'display_name': ctx.display_name or ctx.name,
            'is_default': ctx.is_default,
            'url_prefix': ctx.url_prefix,
            'enabled': ctx.enabled,
        } for ctx in self.enabled_subsystems()]

    # ------------------------------------------------------------- Build

    @classmethod
    def build_from_config(
            cls,
            config,
            *,
            mongodb_params: Dict[str, Any],
            db_name: str = 'IntelligenceIntegrationSystem',
            config_root: Optional[str] = None,
    ) -> 'SubsystemRegistry':
        """
        从 config.json 的 `intelligence_hub.subsystems` 段构建注册表。
        配置段缺失时自动退化为单一默认子系统（向前兼容）。
        """
        subsystems_cfg = config.get('intelligence_hub.subsystems', None)
        default_name = 'news'
        entries: List[Dict[str, Any]] = []
        if subsystems_cfg:
            default_name = str(subsystems_cfg.get('default') or 'news').strip() or 'news'
            entries = list(subsystems_cfg.get('list') or [])
        if not entries:
            entries = [{'name': default_name, 'display_name': default_name}]

        # 保证默认子系统一定在列表中且启用（根路径/未具名数据必须有一个归属）
        default_entry = next(
            (e for e in entries if str(e.get('name') or '').strip() == default_name), None)
        if default_entry is None:
            entries.insert(0, {'name': default_name, 'display_name': default_name})
        elif not default_entry.get('enabled', True):
            logger.warning(f"Default subsystem '{default_name}' disabled by config, force enabled.")
            default_entry['enabled'] = True

        # 内置 dry_run 测试子系统：未显式配置时自动登记（显式配置可覆盖/禁用）
        if not any(str(e.get('name') or '').strip() == DRY_RUN_SUBSYSTEM_NAME for e in entries):
            entries.append({'name': DRY_RUN_SUBSYSTEM_NAME, 'display_name': '干跑测试'})

        registry = cls(default_name=default_name)

        for entry in entries:
            name = str(entry.get('name') or '').strip()
            if not name:
                continue
            enabled = bool(entry.get('enabled', True))
            if not enabled:
                logger.info(f"Subsystem '{name}' disabled by config, skip.")
                continue

            detail = cls._load_detail(entry, config_root)
            is_default = (name == default_name)
            collection_prefix = str(
                detail.get('collection_prefix')
                or (LEGACY_COLLECTION_PREFIX if is_default else f"{name}_intelligence_"))
            url_prefix = str(detail.get('url_prefix') or ('' if is_default else f"/{name}")).strip()

            # ?????????? UI ?? / schema ???????????????
            config_dir = _resolve_subsystem_dir(name)
            plugin_files = _discover_plugin_files(config_dir)
            ui_plugin_enabled = bool(detail.get('ui_plugin', True)) and bool(plugin_files)
            detail_page = detail.get('detail_page')
            schema_meta = _load_schema_meta(config_dir)

            ctx = SubsystemContext(
                name=name,
                display_name=str(detail.get('display_name') or name),
                is_default=is_default,
                enabled=True,
                url_prefix=url_prefix,
                collection_prefix=collection_prefix,
                prompt_table=dict(EVENT_ANALYSIS_PROMPT_TABLE),
                ai_client_group=detail.get('ai_client_group'),
                scoring_config=detail.get('scoring'),
                config_dir=config_dir,
                plugin_files=plugin_files,
                ui_plugin_enabled=ui_plugin_enabled,
                detail_page=detail_page,
                schema_meta=schema_meta,
            )

            # -------- 数据库访问实例（每个子系统一套，互不干扰） --------
            mongodb_params = dict(mongodb_params or {})
            ctx.mongo_db_cache = MongoDBStorage(
                host=mongodb_params.get('host', 'localhost'),
                port=mongodb_params.get('port', 27017),
                db_name=db_name,
                username=mongodb_params.get('username'),
                password=mongodb_params.get('password'),
                collection_name=ctx.collection_name('cached'))
            # -------- Event V4 存储 --------
            database = ctx.mongo_db_cache.db
            ctx.event_v4_intelligence_collection = database[
                ctx.collection_name('v4_archived')]
            ctx.event_v4_intelligence_storage = MongoDBStorage.from_collection(
                ctx.event_v4_intelligence_collection)
            ctx.event_v4_low_value_collection = database[
                ctx.collection_name('v4_low_value')]
            ctx.event_v4_event_collection = database[
                ctx.collection_name('v4_events')]
            ctx.event_v4_entity_collection = database[
                ctx.collection_name('v4_entities')]
            ctx.event_v4_outbox_collection = database[
                ctx.collection_name('v4_outbox')]

            entity_repository = MongoEntityRepository(ctx.event_v4_entity_collection)
            archive_repository = MongoEventV4ArchiveRepository(
                intelligence_collection=ctx.event_v4_intelligence_collection,
                low_value_collection=ctx.event_v4_low_value_collection,
                event_collection=ctx.event_v4_event_collection,
                outbox_collection=ctx.event_v4_outbox_collection,
            )
            entity_repository.ensure_indexes()
            archive_repository.ensure_indexes()
            recovered = archive_repository.recover_pending()
            if recovered:
                logger.warning(
                    "Subsystem '%s' recovered %s pending Event V4 commits.",
                    name, recovered)
            ctx.event_v4_archive_repository = archive_repository
            ctx.event_v4_entity_resolver = DeterministicEntityResolver(entity_repository)
            ctx.event_v4_query_engine = EventV4QueryEngine(
                ctx.event_v4_intelligence_collection,
                ctx.event_v4_event_collection,
                ctx.event_v4_entity_collection,
            )
            ctx.statistics_engine = IntelligenceStatisticsEngine(
                ctx.event_v4_intelligence_collection)

            registry.add(ctx)
            logger.info(
                f"Subsystem '{name}' ready: collections={ctx.collection_prefix}*, "
                f"url_prefix='{url_prefix}', prompts={sorted(ctx.prompt_table)}")

        return registry

    @staticmethod
    def _load_detail(entry: Dict[str, Any], config_root: Optional[str]) -> Dict[str, Any]:
        """子系统入口 + 分离的详细配置文件合并（入口字段优先）。"""
        detail = dict(entry)
        config_file = entry.get('config_file')
        if config_file:
            path = _resolve_path(str(config_file), config_root)
            if os.path.isfile(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        detail.update(json.load(f))
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning(f"Load subsystem config file failed: {path} ({e})")
            else:
                logger.warning(f"Subsystem config file not found: {path}")
        return detail
