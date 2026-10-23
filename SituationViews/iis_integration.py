"""SituationViews 在 IIS 主站中的可选装配适配器。"""

from __future__ import annotations

import logging
from pathlib import Path

from .geocoding import CachedNominatimGeocoder
from .web import SituationViewsService


logger = logging.getLogger(__name__)


def mount_to_iis(app, *, config, subsystem_registry, wrapper, data_path,
                 dashboard=None, public_scoped=False,
                 entity_resolution_service=None, entity_alias_service=None):
    """将态势视图挂到 IIS；实体编辑能力可选，任何失败都不阻断主服务。"""
    app.config["SITUATION_VIEWS_ENABLED"] = False
    settings = config.get("intelligence_hub_web_service.situation_views", {}) or {}
    if not isinstance(settings, dict):
        logger.warning("态势视图配置不是对象，使用默认配置。")
        settings = {}
    if not settings.get("enabled", True):
        logger.info("SituationViews disabled by configuration.")
        return None

    try:
        geocoder = _build_geocoder(settings, data_path)
        service = SituationViewsService(
            subsystem_registry,
            detail_origin="",
            geocoder=geocoder,
            dashboard=dashboard if dashboard is not None else settings.get("dashboard"),
            entity_resolution_service=entity_resolution_service,
            entity_alias_service=entity_alias_service,
        )
        service.mount_to_app(
            app,
            wrapper=wrapper,
            url_prefix="/situation",
            public_scoped=public_scoped,
        )
        app.config["SITUATION_VIEWS_ENABLED"] = True
        logger.info("SituationViews components mounted at '/situation/embed/*'.")
        return service
    except Exception:
        logger.exception("态势视图初始化失败，IIS 主服务继续运行。")
        return None


def _build_geocoder(settings, data_path):
    geocoding = settings.get("geocoding", {}) or {}
    if not isinstance(geocoding, dict):
        logger.warning("态势视图地名解析配置不是对象，使用默认配置。")
        geocoding = {}
    if not geocoding.get("enabled", True):
        return None

    cache_file = Path(
        str(geocoding.get("cache_file") or "SituationViews/geocoding.sqlite"))
    if not cache_file.is_absolute():
        cache_file = Path(data_path) / cache_file
    try:
        return CachedNominatimGeocoder(
            cache_file,
            request_interval=geocoding.get("request_interval_sec", 1.05),
            timeout=geocoding.get("timeout_sec", 12),
        )
    except Exception:
        # 坐标细化失败时仍保留国家级热点和实体关系图。
        logger.exception("态势视图地名解析器初始化失败，将仅显示国家级热点。")
        return None
