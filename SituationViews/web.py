"""实验性态势视图的可拆卸 Flask 挂载层。"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY
from .engine import CATEGORY_LABELS, ENTITY_TYPE_GROUPS, SituationViewEngine, ViewQuery


class SituationViewsService:
    def __init__(self, subsystem_registry, registry=None, *, detail_origin="", geocoder=None):
        self.subsystem_registry = subsystem_registry
        self.registry = registry or DEFAULT_EVENT_REGISTRY
        self.detail_origin = str(detail_origin or "").rstrip("/")
        self.geocoder = geocoder
        self._engines = {}

    def mount_to_app(self, app, *, wrapper=None, url_prefix="/experimental/situation"):
        template_folder = str(Path(__file__).with_name("templates"))
        bp = Blueprint("situation_views", __name__, template_folder=template_folder)
        protect = wrapper or (lambda func: func)

        @bp.get("/")
        @protect
        def index():
            return render_template(
                "situation_views.html",
                categories=CATEGORY_LABELS,
                subsystems=self.subsystem_registry.describe(),
                detail_origin=self.detail_origin,
            )

        @bp.get("/api/map")
        @protect
        def map_api():
            try:
                engine, query = self._request_context()
                return jsonify(engine.map_view(query))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        @bp.get("/api/entity-graph")
        @protect
        def entity_graph_api():
            try:
                engine, query = self._request_context()
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
        @protect
        def entity_graph_intelligences_api():
            try:
                engine, query = self._request_context()
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
        @protect
        def map_intelligences_api():
            try:
                engine, query = self._request_context()
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
        @protect
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

    def _request_context(self):
        subsystem = request.args.get("subsystem")
        ctx = self.subsystem_registry.resolve(subsystem)
        if ctx is None:
            raise ValueError(f"未知子系统: {subsystem}")
        now = dt.datetime.now(dt.timezone.utc)
        start = self._datetime_arg("start", now - dt.timedelta(days=7))
        end = self._datetime_arg("end", now)
        if start >= end:
            raise ValueError("start 必须早于 end")
        min_score = self._bounded_float("min_score", 6.0, 0, 10)
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
            primary_only=self._bool_arg("primary_only", True),
            max_articles=self._bounded_int("max_articles", 5000, 100, 20000),
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
