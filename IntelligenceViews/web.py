"""情报三视图的纯展示集成层。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from urllib.parse import urlencode

from flask import Blueprint, render_template, request


VIEW_NAMES = frozenset({"list", "map", "entities"})


class IntelligenceViewsService:
    """只编排既有子页面，不访问数据库或导入其业务实现。"""

    def __init__(self, subsystems, *, settings=None, situation_prefix="/situation"):
        self.subsystems = tuple(dict(item) for item in (subsystems or ()))
        if not self.subsystems:
            raise ValueError("情报视图至少需要一个可用子系统。")
        self.settings = dict(settings or {}) if isinstance(settings, dict) else {}
        self.situation_prefix = "/" + str(situation_prefix or "situation").strip("/")

    def mount_to_app(self, app, *, wrapper=None, url_prefix="/views"):
        template_folder = str(Path(__file__).with_name("templates"))
        bp = Blueprint("intelligence_views", __name__, template_folder=template_folder)
        protect = wrapper or (lambda func: func)

        @bp.get("/")
        @protect
        def index():
            try:
                profile = self._request_profile()
            except ValueError as exc:
                return str(exc), 400
            return render_template(
                "intelligence_views.html",
                profile=profile,
                frame_urls=self._frame_urls(profile),
            )

        app.register_blueprint(bp, url_prefix=url_prefix)

    def _request_profile(self):
        default_subsystem = next(
            (item for item in self.subsystems if item.get("is_default")),
            self.subsystems[0],
        )
        requested_subsystem = str(
            request.args.get("subsystem") or default_subsystem.get("name") or "").strip()
        subsystem = next(
            (item for item in self.subsystems
             if str(item.get("name") or "") == requested_subsystem),
            None,
        )
        if subsystem is None:
            raise ValueError(f"未知子系统: {requested_subsystem}")

        now = dt.datetime.now(dt.timezone.utc)
        window_days = self._bounded_int_setting("window_days", 7, 1, 366)
        end = self._datetime_arg("end", now)
        start = self._datetime_arg("start", end - dt.timedelta(days=window_days))
        if start >= end:
            raise ValueError("start 必须早于 end")
        max_window_days = self._bounded_int_setting(
            "max_window_days", 366, 1, 3660)
        if end - start > dt.timedelta(days=max_window_days):
            raise ValueError(f"时间范围不能超过 {max_window_days} 天")

        min_score = self._bounded_float(
            request.args.get("min_score", self.settings.get("min_score", 6.0)),
            "min_score", 0, 10,
        )
        active_view = str(
            request.args.get("view") or self.settings.get("default_view") or "list"
        ).strip().lower()
        if active_view not in VIEW_NAMES:
            active_view = "list"

        return {
            "subsystem": str(subsystem.get("name") or ""),
            "subsystem_display_name": str(
                subsystem.get("display_name") or subsystem.get("name") or ""),
            "subsystem_url_prefix": str(subsystem.get("url_prefix") or ""),
            "is_default_subsystem": bool(subsystem.get("is_default")),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "min_score": min_score,
            "active_view": active_view,
            "min_support": self._bounded_int_setting("min_support", 2, 1, 50),
            "max_nodes": self._bounded_int_setting("max_nodes", 80, 10, 300),
            "keep_high_score_singletons": self._bool_setting(
                "keep_high_score_singletons", True),
        }

    def _frame_urls(self, profile):
        list_base = "/intelligences"
        if not profile["is_default_subsystem"]:
            prefix = profile["subsystem_url_prefix"] or f"/{profile['subsystem']}"
            list_base = f"/{prefix.strip('/')}/intelligences"

        list_query = urlencode({
            "threshold": profile["min_score"],
            "sort_by": "content_time",
        })
        shared = {
            "subsystem": profile["subsystem"],
            "start": profile["start"],
            "end": profile["end"],
            "min_score": profile["min_score"],
        }
        graph = {
            **shared,
            "min_support": profile["min_support"],
            "max_nodes": profile["max_nodes"],
            "keep_high_score_singletons": (
                "true" if profile["keep_high_score_singletons"] else "false"),
        }
        return {
            "list": f"{list_base}?{list_query}",
            "map": f"{self.situation_prefix}/embed/map?{urlencode(shared)}",
            "entities": (
                f"{self.situation_prefix}/embed/entities?{urlencode(graph)}"),
        }

    def _bounded_int_setting(self, name, default, minimum, maximum):
        try:
            value = int(self.settings.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _bool_setting(self, name, default):
        value = self.settings.get(name)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _datetime_arg(name, default):
        raw = str(request.args.get(name) or "").strip()
        if not raw:
            return default
        try:
            value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} 不是有效 ISO 时间") from exc
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)

    @staticmethod
    def _bounded_float(value, name, minimum, maximum):
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} 必须是数字") from exc
        if not minimum <= number <= maximum:
            raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
        return number
