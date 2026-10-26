"""把情报三视图集成页作为可选 Blueprint 挂载到 IIS。"""

from __future__ import annotations

import logging

from .web import IntelligenceViewsService


logger = logging.getLogger(__name__)


def mount_to_iis(app, *, config, domain_resources=None, subsystem_registry=None,
                 wrapper=None):
    app.config["INTELLIGENCE_VIEWS_ENABLED"] = False
    settings = config.get("intelligence_hub_web_service.intelligence_views", {}) or {}
    if not isinstance(settings, dict):
        logger.warning("情报视图配置不是对象，使用默认配置。")
        settings = {}
    if not settings.get("enabled", True):
        logger.info("IntelligenceViews disabled by configuration.")
        return None
    try:
        service = IntelligenceViewsService(
            (domain_resources or subsystem_registry).describe_domains(),
            settings=settings,
            situation_prefix=settings.get("situation_prefix", "/situation"),
        )
        service.mount_to_app(
            app,
            wrapper=(None if settings.get("public_access", True) else wrapper),
            url_prefix=settings.get("url_prefix", "/views"),
        )
        app.config["INTELLIGENCE_VIEWS_ENABLED"] = True
        logger.info("IntelligenceViews mounted.")
        return service
    except Exception:
        logger.exception("情报三视图集成页初始化失败，IIS 主服务继续运行。")
        return None

