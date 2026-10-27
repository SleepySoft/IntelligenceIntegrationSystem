"""情报多视图的纯展示集成层。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from flask import Blueprint, render_template, request, session

from WebI18n import ensure_web_i18n


VIEW_NAMES = frozenset({"list", "map", "entities", "timeline"})
LIST_QUERY_NAMES = (
    "page", "per_page", "threshold", "sort_by", "search_mode", "reference",
    "score_threshold",
)
TIMELINE_QUERY_NAMES = ("mode", "orientation")


class IntelligenceViewsService:
    """只编排既有子页面，不访问数据库或导入其业务实现。"""

    def __init__(self, domains, *, settings=None, situation_prefix="/situation"):
        self.domains = tuple(dict(item) for item in (domains or ()))
        if not self.domains:
            raise ValueError("情报视图至少需要一个可用情报域。")
        self.settings = dict(settings or {}) if isinstance(settings, dict) else {}
        self.public_access = bool(self.settings.get("public_access", True))
        self.situation_prefix = "/" + str(situation_prefix or "situation").strip("/")
        self.default_domain = next(
            (item for item in self.domains if item.get("is_default")),
            self.domains[0],
        )
        self.situation = self._situation_profile(
            self.settings.get("situation"),
            domain_name=str(self.default_domain.get("name") or ""),
        )

    def mount_to_app(self, app, *, wrapper=None, url_prefix="/views"):
        ensure_web_i18n(app)
        template_folder = str(Path(__file__).with_name("templates"))
        protect = wrapper or (lambda func: func)
        root_prefix = "/" + str(url_prefix or "views").strip("/")

        bp = Blueprint("intelligence_views", __name__, template_folder=template_folder)
        @bp.get("/")
        @protect
        def index():
            profile = self._page_profile(self.default_domain, views_prefix=root_prefix)
            default_prefix = self._domain_prefix(self.default_domain)
            return render_template(
                "intelligence_views.html",
                profile=profile,
                frame_urls=self._frame_urls(
                    profile,
                    list_prefix="",
                    situation_prefix=f"{default_prefix}/situation",
                ),
            )

        app.register_blueprint(bp, url_prefix=root_prefix)

        for domain in self.domains:
            name = str(domain.get("name") or "").strip()
            if not name:
                continue
            domain_prefix = self._domain_prefix(domain)
            named = Blueprint(
                f"intelligence_views_{self._endpoint_name(name)}",
                __name__,
                template_folder=template_folder,
            )

            def named_index(selected_domain=domain, selected_prefix=domain_prefix):
                profile = self._page_profile(
                    selected_domain, views_prefix=root_prefix)
                return render_template(
                    "intelligence_views.html",
                    profile=profile,
                    frame_urls=self._frame_urls(
                        profile,
                        list_prefix=selected_prefix,
                        situation_prefix=f"{selected_prefix}/situation",
                    ),
                )

            named.add_url_rule("/", "index", protect(named_index), methods=["GET"])
            app.register_blueprint(
                named,
                url_prefix=f"{domain_prefix}{root_prefix}",
            )

    def _situation_profile(self, raw, *, domain_name=""):
        values = dict(raw or {}) if isinstance(raw, dict) else {}
        requested_domain = str(
            domain_name or values.get("domain_name") or values.get("subsystem")
            or self.default_domain.get("name") or ""
        ).strip()
        domain = next(
            (item for item in self.domains
             if str(item.get("name") or "") == requested_domain),
            None,
        )
        if domain is None:
            domain = self.default_domain
        window_days = self._bounded_int(values, "window_days", 7, 1, 365)
        public_window_days = self._bounded_int(
            values, "public_window_days", window_days, 1, window_days)
        return {
            "domain_name": str(domain.get("name") or ""),
            "domain_display_name": str(
                domain.get("display_name") or domain.get("name") or ""),
            "window_days": window_days,
            "public_window_days": public_window_days,
            "timeline_max_entities": self._bounded_int(
                values, "timeline_max_entities", 8, 1, 8),
            "public_timeline_max_entities": self._bounded_int(
                values, "public_timeline_max_entities", 8, 1, 8),
            "min_score": self._bounded_float(values, "min_score", 6.0, 0, 10),
            "min_support": self._bounded_int(values, "min_support", 2, 1, 50),
        }

    def _page_profile(self, domain, *, views_prefix="/views"):
        active_view = str(
            request.args.get("view") or self.settings.get("default_view") or "list"
        ).strip().lower()
        if active_view not in VIEW_NAMES:
            active_view = "list"

        domain_name = str(domain.get("name") or "")
        situation = self._situation_profile(
            self.settings.get("situation"), domain_name=domain_name)
        if self.public_access and not session.get("logged_in", False):
            situation["window_days"] = situation["public_window_days"]
            situation["timeline_max_entities"] = min(
                situation["timeline_max_entities"],
                situation["public_timeline_max_entities"],
            )
        return {
            "active_view": active_view,
            "domain_name": domain_name,
            "domain_display_name": str(
                domain.get("display_name") or domain_name),
            "domain_options": [
                {
                    "name": str(item.get("name") or ""),
                    "display_name": str(
                        item.get("display_name") or item.get("name") or ""),
                    "views_url": f"{self._domain_prefix(item)}{views_prefix}/",
                }
                for item in self.domains
                if str(item.get("name") or "").strip()
            ],
            "situation": situation,
        }

    def _frame_urls(self, _profile, *, list_prefix="", situation_prefix=""):
        list_query = {
            name: request.args.get(name)
            for name in LIST_QUERY_NAMES
            if request.args.get(name) not in (None, "")
        }
        list_query.setdefault("threshold", "6")
        timeline_query = []
        entity_limit = int(_profile["situation"]["timeline_max_entities"])
        for entity_id in request.args.getlist("entity")[:entity_limit]:
            entity_id = str(entity_id or "").strip()
            if entity_id:
                timeline_query.append(("entity", entity_id))
        timeline_query.extend(
            (name, request.args.get(name))
            for name in TIMELINE_QUERY_NAMES
            if request.args.get(name) not in (None, "")
        )
        timeline_suffix = urlencode(timeline_query)
        situation_prefix = situation_prefix or self.situation_prefix
        return {
            "list": f"{list_prefix}/intelligences?{urlencode(list_query)}",
            "map": f"{situation_prefix}/embed/map",
            "entities": f"{situation_prefix}/embed/entities",
            "timeline": (
                f"{situation_prefix}/embed/timeline"
                f"{f'?{timeline_suffix}' if timeline_suffix else ''}"
            ),
        }

    @staticmethod
    def _domain_prefix(domain):
        name = str(domain.get("name") or "").strip()
        configured = str(domain.get("url_prefix") or "").strip().rstrip("/")
        return configured or f"/{name}"

    @staticmethod
    def _endpoint_name(value):
        return "".join(char if char.isalnum() or char == "_" else "_" for char in value)

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
