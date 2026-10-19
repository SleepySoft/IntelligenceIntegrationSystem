"""“态势洞察”的可拆卸 Flask 挂载层。"""

from __future__ import annotations

import datetime as dt
import re
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY
from .engine import CATEGORY_LABELS, ENTITY_TYPE_GROUPS, SituationViewEngine, ViewQuery


class SituationViewsService:
    def __init__(self, subsystem_registry, registry=None, *, detail_origin="", geocoder=None,
                 dashboard=None):
        self.subsystem_registry = subsystem_registry
        self.registry = registry or DEFAULT_EVENT_REGISTRY
        self.detail_origin = str(detail_origin or "").rstrip("/")
        self.geocoder = geocoder
        self.dashboard = self._dashboard_profile(dashboard)
        self._engines = {}

    def mount_to_app(self, app, *, wrapper=None, url_prefix="/situation",
                     public_scoped=False):
        template_folder = str(Path(__file__).with_name("templates"))
        bp = Blueprint("situation_views", __name__, template_folder=template_folder)
        protect = wrapper or (lambda func: func)
        embed_protect = (lambda func: func) if public_scoped else protect

        def scoped_or_protected(func):
            protected = protect(func)

            @wraps(func)
            def dispatch(*args, **kwargs):
                if public_scoped and self._is_scoped_request():
                    return func(*args, **kwargs)
                return protected(*args, **kwargs)

            return dispatch

        @bp.get("/embed/map")
        @embed_protect
        def embedded_map_dashboard():
            return self._render_dashboard(
                "map", profile=self.dashboard, embedded=True)

        @bp.get("/embed/entities")
        @embed_protect
        def embedded_entity_dashboard():
            return self._render_dashboard(
                "graph", profile=self.dashboard, embedded=True)

        @bp.get("/api/map")
        @bp.get("/api/scoped/map")
        @scoped_or_protected
        def map_api():
            try:
                engine, query = self._request_context(fixed=self._is_scoped_request())
                return jsonify(engine.map_view(query))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        @bp.get("/api/entity-graph")
        @bp.get("/api/scoped/entity-graph")
        @scoped_or_protected
        def entity_graph_api():
            try:
                fixed = self._is_scoped_request()
                engine, query = self._request_context(fixed=fixed)
                if fixed:
                    min_support = self.dashboard["min_support"]
                    max_nodes = self.dashboard["max_nodes"]
                    max_edges = self.dashboard["max_edges"]
                    keep_singletons = self.dashboard["keep_high_score_singletons"]
                else:
                    min_support = self._bounded_int("min_support", 2, 1, 50)
                    max_nodes = self._bounded_int("max_nodes", 80, 10, 300)
                    max_edges = self._bounded_int("max_edges", 240, 10, 1000)
                    keep_singletons = self._bool_arg("keep_high_score_singletons", True)
                entity_types = self._set_arg("entity_types", ENTITY_TYPE_GROUPS)
                return jsonify(engine.entity_graph(
                    query,
                    min_support=min_support,
                    max_nodes=max_nodes,
                    max_edges=max_edges,
                    keep_high_score_singletons=keep_singletons,
                    entity_types=entity_types,
                ))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        @bp.get("/api/entity-graph/intelligences")
        @bp.get("/api/scoped/entity-graph/intelligences")
        @scoped_or_protected
        def entity_graph_intelligences_api():
            try:
                engine, query = self._request_context(fixed=self._is_scoped_request())
                selection_type = str(request.args.get("selection_type") or "").strip()
                role = str(request.args.get("role") or "all").strip()
                entity_types = self._set_arg("entity_types", ENTITY_TYPE_GROUPS)
                offset = self._bounded_int("offset", 0, 0, 100000)
                limit = self._bounded_int("limit", 50, 1, 100)
                return jsonify(engine.entity_graph_intelligences(
                    query,
                    selection_type=selection_type,
                    entity_id=str(request.args.get("entity_id") or "").strip() or None,
                    source_id=str(request.args.get("source_id") or "").strip() or None,
                    target_id=str(request.args.get("target_id") or "").strip() or None,
                    predicate=str(request.args.get("predicate") or "").strip() or None,
                    directed=self._bool_arg("directed", True),
                    role=role,
                    entity_types=entity_types,
                    offset=offset,
                    limit=limit,
                ))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        @bp.get("/api/map/intelligences")
        @bp.get("/api/scoped/map/intelligences")
        @scoped_or_protected
        def map_intelligences_api():
            try:
                engine, query = self._request_context(fixed=self._is_scoped_request())
                country_code = request.args.get("country_code")
                location_name = str(request.args.get("location_name") or "").strip() or None
                offset = self._bounded_int("offset", 0, 0, 100000)
                limit = self._bounded_int("limit", 50, 1, 100)
                return jsonify(engine.map_intelligences(
                    query, country_code, location_name=location_name,
                    offset=offset, limit=limit))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        @bp.post("/api/map/locations")
        @bp.post("/api/scoped/map/locations")
        @scoped_or_protected
        def map_locations_api():
            try:
                values = []
                raw_values = (request.get_json(silent=True) or {}).get("locations") or []
                for item in raw_values[:120]:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()[:200]
                    code = str(item.get("country_code") or "").strip().upper()
                    if not name or not re.fullmatch(r"[A-Z]{2}", code):
                        continue
                    try:
                        event_count = max(1, int(item.get("event_count") or 1))
                    except (TypeError, ValueError):
                        event_count = 1
                    values.append({
                        "name": name,
                        "country_code": code,
                        "event_count": event_count,
                        "dominant_category": str(
                            item.get("dominant_category") or "other"),
                    })
                if self.geocoder is None:
                    return jsonify({
                        "locations": [], "pending_count": 0,
                        "unavailable": True,
                    })
                return jsonify(self.geocoder.resolve_many(values))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        app.register_blueprint(bp, url_prefix=url_prefix)

    def _render_dashboard(self, view, *, profile=None, embedded=False):
        return render_template(
            "situation_views.html",
            categories=CATEGORY_LABELS,
            subsystems=self.subsystem_registry.describe(),
            detail_origin=self.detail_origin,
            dashboard_view=view,
            dashboard_profile=profile or self.dashboard,
            embedded=embedded,
            api_prefix="../api/scoped" if embedded else "api",
        )

    def _dashboard_profile(self, raw):
        values = dict(raw or {}) if isinstance(raw, dict) else {}
        descriptions = self.subsystem_registry.describe()
        default = next(
            (item.get("name") for item in descriptions if item.get("is_default")),
            descriptions[0].get("name") if descriptions else "",
        )
        subsystem = str(values.get("subsystem") or default).strip()
        if not any(item.get("name") == subsystem for item in descriptions):
            subsystem = default
        selected = next(
            (item for item in descriptions if item.get("name") == subsystem), {})
        return {
            "subsystem": subsystem,
            "subsystem_display_name": selected.get("display_name") or subsystem,
            "window_days": self._coerce_number(values.get("window_days"), 7, 1, 365, int),
            "min_score": self._coerce_number(values.get("min_score"), 6.0, 0, 10, float),
            "min_support": self._coerce_number(values.get("min_support"), 2, 1, 50, int),
            "max_nodes": self._coerce_number(values.get("max_nodes"), 80, 10, 5000, int),
            "max_edges": self._coerce_number(values.get("max_edges"), 240, 10, 20000, int),
            "max_articles": self._coerce_number(
                values.get("max_articles"), 5000, 100, 20000, int),
            "primary_only": self._coerce_bool(values.get("primary_only"), True),
            "keep_high_score_singletons": self._coerce_bool(
                values.get("keep_high_score_singletons"), True),
        }

    @staticmethod
    def _coerce_number(value, default, minimum, maximum, converter):
        try:
            converted = converter(value if value is not None else default)
        except (TypeError, ValueError):
            converted = converter(default)
        return max(minimum, min(maximum, converted))

    @staticmethod
    def _coerce_bool(value, default):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"0", "false", "no", "off"}

    def _request_context(self, *, fixed=False):
        subsystem = self.dashboard["subsystem"] if fixed else request.args.get("subsystem")
        ctx = self.subsystem_registry.resolve(subsystem)
        if ctx is None:
            raise ValueError(f"未知子系统: {subsystem}")
        now = dt.datetime.now(dt.timezone.utc)
        if fixed:
            end = now
            start = end - dt.timedelta(days=self.dashboard["window_days"])
        else:
            start = self._datetime_arg("start", now - dt.timedelta(days=7))
            end = self._datetime_arg("end", now)
        if start >= end:
            raise ValueError("start 必须早于 end")
        min_score = (
            self.dashboard["min_score"] if fixed
            else self._bounded_float("min_score", 6.0, 0, 10)
        )
        raw_categories = str(request.args.get("category") or "").strip()
        if not raw_categories:
            categories = None
        elif raw_categories == "__none__":
            categories = frozenset()
        else:
            categories = frozenset(
                value.strip() for value in raw_categories.split(",") if value.strip())
            unknown = categories.difference(CATEGORY_LABELS)
            if unknown:
                raise ValueError(f"未知事件类别: {', '.join(sorted(unknown))}")
        query = ViewQuery(
            start=start,
            end=end,
            min_score=min_score,
            categories=categories,
            primary_only=(
                self.dashboard["primary_only"] if fixed
                else self._bool_arg("primary_only", True)
            ),
            max_articles=(
                self.dashboard["max_articles"] if fixed
                else self._bounded_int("max_articles", 5000, 100, 20000)
            ),
        )
        key = ctx.name
        engine = self._engines.get(key)
        if engine is None:
            engine = SituationViewEngine(
                ctx.event_v4_intelligence_collection,
                ctx.event_v4_event_collection,
                self.registry,
            )
            self._engines[key] = engine
        return engine, query

    @staticmethod
    def _is_scoped_request():
        return "/api/scoped/" in request.path

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
    def _bounded_int(name, default, minimum, maximum):
        try:
            value = int(request.args.get(name, default))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} 必须是整数") from exc
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
        return value

    @staticmethod
    def _bounded_float(name, default, minimum, maximum):
        try:
            value = float(request.args.get(name, default))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} 必须是数字") from exc
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
        return value

    @staticmethod
    def _bool_arg(name, default):
        raw = request.args.get(name)
        if raw is None:
            return default
        return str(raw).strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _set_arg(name, allowed):
        raw = str(request.args.get(name) or "").strip()
        if not raw:
            return None
        if raw == "__none__":
            return frozenset()
        values = frozenset(value.strip() for value in raw.split(",") if value.strip())
        unknown = values.difference(allowed)
        if unknown:
            raise ValueError(f"{name} 包含未知值: {', '.join(sorted(unknown))}")
        return values
