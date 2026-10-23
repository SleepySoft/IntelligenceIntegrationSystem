"""情报多视图的纯展示集成层。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from flask import Blueprint, render_template, request


VIEW_NAMES = frozenset({"list", "map", "entities", "timeline"})
LIST_QUERY_NAMES = (
    "page", "per_page", "threshold", "sort_by", "search_mode", "reference",
    "score_threshold",
)
TIMELINE_QUERY_NAMES = ("mode", "orientation")


class IntelligenceViewsService:
    """只编排既有子页面，不访问数据库或导入其业务实现。"""

    def __init__(self, subsystems, *, settings=None, situation_prefix="/situation"):
        self.subsystems = tuple(dict(item) for item in (subsystems or ()))
        if not self.subsystems:
            raise ValueError("情报视图至少需要一个可用子系统。")
        self.settings = dict(settings or {}) if isinstance(settings, dict) else {}
        self.situation_prefix = "/" + str(situation_prefix or "situation").strip("/")
        self.situation = self._situation_profile(self.settings.get("situation"))

    def mount_to_app(self, app, *, wrapper=None, url_prefix="/views"):
        template_folder = str(Path(__file__).with_name("templates"))
        bp = Blueprint("intelligence_views", __name__, template_folder=template_folder)
        protect = wrapper or (lambda func: func)

        @bp.get("/")
        @protect
        def index():
            profile = self._page_profile()
            return render_template(
                "intelligence_views.html",
                profile=profile,
                frame_urls=self._frame_urls(profile),
            )

        app.register_blueprint(bp, url_prefix=url_prefix)

    def _situation_profile(self, raw):
        values = dict(raw or {}) if isinstance(raw, dict) else {}
        default_subsystem = next(
            (item for item in self.subsystems if item.get("is_default")),
            self.subsystems[0],
        )
        requested_subsystem = str(
            values.get("subsystem") or default_subsystem.get("name") or ""
        ).strip()
        subsystem = next(
            (item for item in self.subsystems
             if str(item.get("name") or "") == requested_subsystem),
            None,
        )
        if subsystem is None:
            subsystem = default_subsystem
        return {
            "subsystem": str(subsystem.get("name") or ""),
            "subsystem_display_name": str(
                subsystem.get("display_name") or subsystem.get("name") or ""),
            "window_days": self._bounded_int(values, "window_days", 7, 1, 365),
            "min_score": self._bounded_float(values, "min_score", 6.0, 0, 10),
            "min_support": self._bounded_int(values, "min_support", 2, 1, 50),
        }

    def _page_profile(self):
        active_view = str(
            request.args.get("view") or self.settings.get("default_view") or "list"
        ).strip().lower()
        if active_view not in VIEW_NAMES:
            active_view = "list"

        return {"active_view": active_view, "situation": self.situation}

    def _frame_urls(self, _profile):
        list_query = {
            name: request.args.get(name)
            for name in LIST_QUERY_NAMES
            if request.args.get(name) not in (None, "")
        }
        list_query.setdefault("threshold", "6")
        timeline_query = []
        for entity_id in request.args.getlist("entity")[:8]:
            entity_id = str(entity_id or "").strip()
            if entity_id:
                timeline_query.append(("entity", entity_id))
        timeline_query.extend(
            (name, request.args.get(name))
            for name in TIMELINE_QUERY_NAMES
            if request.args.get(name) not in (None, "")
        )
        timeline_suffix = urlencode(timeline_query)
        return {
            "list": f"/intelligences?{urlencode(list_query)}",
            "map": f"{self.situation_prefix}/embed/map",
            "entities": f"{self.situation_prefix}/embed/entities",
            "timeline": (
                f"{self.situation_prefix}/embed/timeline"
                f"{f'?{timeline_suffix}' if timeline_suffix else ''}"
            ),
        }

    @staticmethod
    def _bounded_int(values, name, default, minimum, maximum):
        try:
            value = int(values.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _bounded_float(values, name, default, minimum, maximum):
        try:
            value = float(values.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))
