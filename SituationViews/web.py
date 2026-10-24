"""“态势洞察”的可拆卸 Flask 挂载层。"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from functools import lru_cache, wraps
from pathlib import Path
from urllib import request as urlrequest

from flask import Blueprint, Response, abort, jsonify, render_template, request, session

from EntityResolution.model import EntityRef, ResolutionError
from EntityResolution.service import EntityResolutionService
from EntityAlias.model import AliasError
from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY
from .engine import CATEGORY_LABELS, ENTITY_TYPE_GROUPS, SituationViewEngine, ViewQuery


logger = logging.getLogger(__name__)

# 与 IISConfig.SituationSettings 的正式容量边界保持一致。这里同时供 API 校验、
# dashboard profile 和模板输入约束使用，避免 UI/API 各自遗留更小的历史上限。
GRAPH_MIN_NODES = 10
GRAPH_MAX_NODES = 5000
GRAPH_MIN_EDGES = 10
GRAPH_MAX_EDGES = 20000

# 浏览器不直接连接公共 CDN：移动网络、DNS 或代理策略经常只让这些请求失败，
# 随后的内联代码就会表现为 ``d3 is not defined``。IIS 只代理固定版本、固定摘要
# 的只读前端资源，并在进程内缓存；客户端仍可使用普通 HTTP 缓存。
_FRONTEND_ASSETS = {
    "d3.min.js": (
        "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js",
        "application/javascript; charset=utf-8",
        "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539",
        300_000,
    ),
    "topojson-client.min.js": (
        "https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js",
        "application/javascript; charset=utf-8",
        "25cd02ae486cc5063e0215a4e4cfb15de83700c87ac48bac4d57dc6aaf3ebb89",
        20_000,
    ),
    "countries-110m.json": (
        "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json",
        "application/json; charset=utf-8",
        "2516c915867c7baf18ddec727aec46c315541a07cfb3d79a6559b05d5e94eee8",
        150_000,
    ),
    "countries.json": (
        "https://cdn.jsdelivr.net/npm/world-countries@5.1.0/countries.json",
        "application/json; charset=utf-8",
        "359431fb9475666dfad1ea5e72e53521cef40520f65eecd08e02ba569eb8491b",
        1_600_000,
    ),
}


@lru_cache(maxsize=len(_FRONTEND_ASSETS))
def _load_frontend_asset(asset_name: str) -> bytes:
    url, _content_type, expected_sha256, maximum_size = _FRONTEND_ASSETS[asset_name]
    upstream_request = urlrequest.Request(
        url,
        headers={"User-Agent": "IIS-SituationViews/1.0"},
    )
    with urlrequest.urlopen(upstream_request, timeout=20) as response:
        payload = response.read(maximum_size + 1)
    if len(payload) > maximum_size:
        raise ValueError(f"frontend asset too large: {asset_name}")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"frontend asset checksum mismatch: {asset_name}")
    return payload


class SituationViewsService:
    def __init__(self, subsystem_registry, registry=None, *, detail_origin="", geocoder=None,
                 dashboard=None, entity_resolution_service=None,
                 entity_alias_service=None):
        self.subsystem_registry = subsystem_registry
        self.registry = registry or DEFAULT_EVENT_REGISTRY
        self.detail_origin = str(detail_origin or "").rstrip("/")
        self.geocoder = geocoder
        self.dashboard = self._dashboard_profile(dashboard)
        self.entity_resolution_service = entity_resolution_service
        self.entity_alias_service = entity_alias_service
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

        @bp.get("/assets/<asset_name>")
        def frontend_asset(asset_name):
            spec = _FRONTEND_ASSETS.get(asset_name)
            if spec is None:
                abort(404)
            try:
                payload = _load_frontend_asset(asset_name)
            except Exception:
                logger.exception("态势视图前端资源加载失败：%s", asset_name)
                return Response("frontend asset unavailable", status=502,
                                content_type="text/plain; charset=utf-8")
            response = Response(payload, content_type=spec[1])
            response.set_etag(spec[2])
            response.cache_control.public = True
            response.cache_control.max_age = 7 * 24 * 60 * 60
            return response.make_conditional(request)

        @bp.get("/embed/map")
        @embed_protect
        def embedded_map_dashboard():
            scoped = public_scoped and not session.get("logged_in", False)
            return self._render_dashboard(
                "map", profile=self._viewer_profile(scoped), embedded=True,
                scoped=scoped)

        @bp.get("/embed/entities")
        @embed_protect
        def embedded_entity_dashboard():
            scoped = public_scoped and not session.get("logged_in", False)
            return self._render_dashboard(
                "graph", profile=self._viewer_profile(scoped), embedded=True,
                scoped=scoped)

        @bp.get("/embed/timeline")
        @embed_protect
        def embedded_entity_timeline():
            scoped = public_scoped and not session.get("logged_in", False)
            return self._render_timeline(
                profile=self._viewer_profile(scoped), embedded=True, scoped=scoped)

        @bp.get("/api/map")
        @bp.get("/api/scoped/map")
        @scoped_or_protected
        def map_api():
            try:
                engine, query = self._request_context(fixed=self._is_scoped_request())
                return jsonify(engine.map_view(query))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        # 地图主投影、证据卡片和细化地点解析保持相邻，避免同一接口族散落。
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

        # 实体图主投影、证据和人工编辑所需引用查询保持为连续路由组。
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
                    max_nodes = self._bounded_int(
                        "max_nodes", 80, GRAPH_MIN_NODES, GRAPH_MAX_NODES)
                    max_edges = self._bounded_int(
                        "max_edges", 240, GRAPH_MIN_EDGES, GRAPH_MAX_EDGES)
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

        @bp.get("/api/entity-graph/references")
        @protect
        def entity_graph_references_api():
            try:
                engine, query = self._request_context(fixed=False)
                return jsonify(engine.entity_graph_references(
                    query, str(request.args.get("entity_id") or "").strip()))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        # 实体时间线主投影和候选实体检索保持相邻。
        @bp.get("/api/entity-timeline")
        @bp.get("/api/scoped/entity-timeline")
        @scoped_or_protected
        def entity_timeline_api():
            try:
                scoped = self._is_scoped_request()
                engine, query = self._request_context(fixed=scoped)
                maximum = self._timeline_entity_limit(scoped)
                return jsonify(engine.entity_timeline(
                    query, entity_ids=self._entity_args(maximum),
                    max_entities=maximum))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        @bp.get("/api/entity-timeline/entities")
        @bp.get("/api/scoped/entity-timeline/entities")
        @scoped_or_protected
        def entity_timeline_entities_api():
            try:
                engine, query = self._request_context(fixed=self._is_scoped_request())
                return jsonify(engine.entity_timeline_entities(
                    query,
                    search=str(request.args.get("q") or "").strip()[:200],
                    limit=self._bounded_int("limit", 30, 1, 50),
                ))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        @bp.get("/api/entity-resolution/global-search")
        @protect
        def entity_global_search_api():
            if self.entity_alias_service is None:
                return jsonify({"error": "全局实体注册表未启用"}), 503
            query = str(request.args.get("q") or "").strip()
            if not query:
                return jsonify({"items": []})
            lookup = self.entity_alias_service.lookup(
                query, limit=10, min_score=0.35, use_vector=True)
            values = []
            if lookup.get("exact"):
                exact = lookup["exact"]
                values.append({
                    "id": exact["entity_id"], "name": exact["canonical"],
                    "type": exact.get("type") or "other",
                    "description": exact.get("description") or "",
                    "status": exact.get("status") or "unverified",
                    "match": "exact",
                })
            for candidate in lookup.get("candidates") or []:
                values.append({
                    "id": candidate["entity_id"], "name": candidate["canonical"],
                    "type": candidate.get("type") or "other",
                    "description": candidate.get("description") or "",
                    "status": candidate.get("status") or "unverified",
                    "match": candidate.get("source") or "candidate",
                    "score": candidate.get("score"),
                })
            seen = set()
            return jsonify({"items": [
                item for item in values
                if not (item["id"] in seen or seen.add(item["id"]))
            ]})

        @bp.post("/api/entity-resolution/global-entities")
        @protect
        def entity_global_create_api():
            if self.entity_alias_service is None:
                return jsonify({"error": "全局实体注册表未启用"}), 503
            data = request.get_json(silent=True) or {}
            try:
                entity = self.entity_alias_service.create_entity(
                    str(data.get("name") or "").strip(),
                    type=str(data.get("type") or "other").strip(),
                    description=str(data.get("description") or "").strip(),
                    status="verified",
                )
                return jsonify(entity), 201
            except AliasError as exc:
                return jsonify({"error": str(exc), "code": exc.code}), \
                    {"not_found": 404, "conflict": 409}.get(exc.code, 400)

        @bp.post("/api/entity-resolution/fixed-aliases")
        @protect
        def entity_fixed_alias_create_api():
            """登记全局固定别名；此操作不创建任何 EntityRef/事件关联。"""
            if self.entity_alias_service is None:
                return jsonify({"error": "全局实体注册表未启用"}), 503
            data = request.get_json(silent=True) or {}
            target_id = str(data.get("target_id") or "").strip()
            raw_names = data.get("names")
            if raw_names is None:
                raw_names = [data.get("name")]
            if not isinstance(raw_names, list):
                return jsonify({"error": "names 必须是名称数组"}), 400
            names = list(dict.fromkeys(
                str(value or "").strip() for value in raw_names
                if str(value or "").strip()
            ))
            if not target_id or not names:
                return jsonify({"error": "target_id 和至少一个名称不能为空"}), 400
            if len(names) > 20:
                return jsonify({"error": "单次最多登记 20 个固定别名"}), 400
            try:
                if self.entity_alias_service.get_entity(target_id) is None:
                    raise AliasError(f"实体不存在：{target_id}", "not_found")
                existing = {}
                for name in names:
                    hit = self.entity_alias_service.resolve(name)
                    if hit and hit["entity_id"] != target_id:
                        raise AliasError(
                            f"名称 {name!r} 已属于其它全局实体", "conflict")
                    existing[name] = bool(hit)
                registered = []
                for name in names:
                    if not existing[name]:
                        self.entity_alias_service.add_alias(target_id, name)
                        registered.append(name)
                return jsonify({
                    "target_id": target_id,
                    "registered": registered,
                    "already_registered": [name for name in names if existing[name]],
                })
            except AliasError as exc:
                return jsonify({"error": str(exc), "code": exc.code}), \
                    {"not_found": 404, "conflict": 409}.get(exc.code, 400)

        @bp.get("/api/entity-resolution/contextual-search")
        @protect
        def entity_contextual_search_api():
            if self.entity_resolution_service is None:
                return jsonify({"error": "实体解析服务未启用"}), 503
            try:
                subsystem = str(request.args.get("subsystem") or "").strip()
                items = self.entity_resolution_service.search_contextual(
                    subsystem, str(request.args.get("q") or "").strip(), limit=20)
                return jsonify({"items": items})
            except ResolutionError as exc:
                return self._resolution_error(exc)

        @bp.post("/api/entity-resolution/contextual-entities")
        @protect
        def entity_contextual_create_api():
            if self.entity_resolution_service is None:
                return jsonify({"error": "实体解析服务未启用"}), 503
            data = request.get_json(silent=True) or {}
            try:
                entity = self.entity_resolution_service.create_contextual(
                    str(data.get("subsystem") or "").strip(),
                    str(data.get("name") or "").strip(),
                    actor=str(session.get("username") or "unknown"),
                    optional_fields={key: data.get(key) for key in (
                        "type", "countries", "locations", "time_range", "topic",
                        "description")},
                )
                return jsonify(self._json_safe(entity)), 201
            except ResolutionError as exc:
                return self._resolution_error(exc)

        @bp.post("/api/entity-resolution/assign")
        @protect
        def entity_assign_api():
            if self.entity_resolution_service is None:
                return jsonify({"error": "实体解析服务未启用"}), 503
            data = request.get_json(silent=True) or {}
            try:
                refs = self._resolution_refs(data)
                target_kind = str(data.get("target_kind") or "").strip()
                target_id = str(data.get("target_id") or "").strip()
                actor = str(session.get("username") or "unknown")
                reason = str(data.get("reason") or "").strip()
                if target_kind == "global":
                    # 兼容旧调用方；新界面的固定别名使用独立端点，不与具体引用绑定。
                    if data.get("register_fixed_alias"):
                        if self.entity_alias_service is None:
                            raise ResolutionError("全局实体注册表未启用")
                        if len(refs) != 1:
                            raise ResolutionError("旧版固定别名参数只支持一个引用")
                        source = self.entity_resolution_service.get_reference(refs[0])
                        surface = str(source.get("name") or "").strip()
                        existing = self.entity_alias_service.resolve(surface)
                        if existing and existing["entity_id"] != target_id:
                            raise ResolutionError(
                                f"名称 {surface!r} 已属于其它全局实体", "conflict")
                        if not existing:
                            self.entity_alias_service.add_alias(target_id, surface)
                result = self.entity_resolution_service.assign_many(
                    refs, target_kind, target_id, actor=actor, reason=reason)
                return jsonify(self._json_safe(result))
            except (ResolutionError, AliasError) as exc:
                if isinstance(exc, ResolutionError):
                    return self._resolution_error(exc)
                return jsonify({"error": str(exc), "code": exc.code}), \
                    {"not_found": 404, "conflict": 409}.get(exc.code, 400)

        @bp.post("/api/entity-resolution/unassign")
        @protect
        def entity_unassign_api():
            if self.entity_resolution_service is None:
                return jsonify({"error": "实体解析服务未启用"}), 503
            data = request.get_json(silent=True) or {}
            try:
                result = self.entity_resolution_service.unassign_many(
                    self._resolution_refs(data),
                    actor=str(session.get("username") or "unknown"),
                    reason=str(data.get("reason") or "").strip(),
                )
                return jsonify(self._json_safe(result))
            except ResolutionError as exc:
                return self._resolution_error(exc)

        @bp.post("/api/entity-resolution/undo")
        @protect
        def entity_undo_api():
            if self.entity_resolution_service is None:
                return jsonify({"error": "实体解析服务未启用"}), 503
            data = request.get_json(silent=True) or {}
            try:
                result = self.entity_resolution_service.undo(
                    str(data.get("subsystem") or "").strip(),
                    str(data.get("decision_id") or "").strip(),
                    actor=str(session.get("username") or "unknown"),
                )
                return jsonify(self._json_safe(result))
            except ResolutionError as exc:
                return self._resolution_error(exc)

        app.register_blueprint(bp, url_prefix=url_prefix)

    def _render_dashboard(self, view, *, profile=None, embedded=False, scoped=None):
        if scoped is None:
            scoped = embedded
        return render_template(
            "situation_views.html",
            categories=CATEGORY_LABELS,
            predicate_labels={
                str(predicate_id): str(spec.label or predicate_id)
                for predicate_id, spec in self.registry.items()
            },
            subsystems=self.subsystem_registry.describe(),
            detail_origin=self.detail_origin,
            dashboard_view=view,
            dashboard_profile=profile or self.dashboard,
            embedded=embedded,
            api_prefix="../api/scoped" if scoped else ("../api" if embedded else "api"),
            edit_api_prefix="../api" if embedded else "api",
            entity_edit_enabled=bool(
                self.entity_resolution_service is not None
                and session.get("logged_in", False)),
            graph_max_nodes=GRAPH_MAX_NODES,
        )

    def _render_timeline(self, *, profile=None, embedded=False, scoped=None):
        if scoped is None:
            scoped = embedded
        return render_template(
            "entity_timeline.html",
            categories=CATEGORY_LABELS,
            subsystems=self.subsystem_registry.describe(),
            detail_origin=self.detail_origin,
            dashboard_profile=profile or self.dashboard,
            embedded=embedded,
            api_prefix="../api/scoped" if scoped else ("../api" if embedded else "api"),
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
        window_days = self._coerce_number(values.get("window_days"), 7, 1, 365, int)
        public_window_days = self._coerce_number(
            values.get("public_window_days"), window_days, 1, window_days, int)
        return {
            "subsystem": subsystem,
            "subsystem_display_name": selected.get("display_name") or subsystem,
            "window_days": window_days,
            "public_window_days": public_window_days,
            "timeline_max_entities": self._coerce_number(
                values.get("timeline_max_entities"), 8, 1, 8, int),
            "public_timeline_max_entities": self._coerce_number(
                values.get("public_timeline_max_entities"), 8, 1, 8, int),
            "min_score": self._coerce_number(values.get("min_score"), 6.0, 0, 10, float),
            "min_support": self._coerce_number(values.get("min_support"), 2, 1, 50, int),
            "max_nodes": self._coerce_number(
                values.get("max_nodes"), 80, GRAPH_MIN_NODES, GRAPH_MAX_NODES, int),
            "max_edges": self._coerce_number(
                values.get("max_edges"), 240, GRAPH_MIN_EDGES, GRAPH_MAX_EDGES, int),
            "max_articles": self._coerce_number(
                values.get("max_articles"), 5000, 100, 20000, int),
            "primary_only": self._coerce_bool(values.get("primary_only"), True),
            "keep_high_score_singletons": self._coerce_bool(
                values.get("keep_high_score_singletons"), True),
        }

    def _viewer_profile(self, public: bool):
        profile = dict(self.dashboard)
        if public:
            profile["window_days"] = profile["public_window_days"]
            profile["timeline_max_entities"] = self._timeline_entity_limit(True)
        return profile

    def _timeline_entity_limit(self, public: bool) -> int:
        maximum = int(self.dashboard["timeline_max_entities"])
        if public:
            maximum = min(maximum, int(self.dashboard["public_timeline_max_entities"]))
        return maximum

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
            start = end - dt.timedelta(days=self.dashboard["public_window_days"])
        else:
            start = self._datetime_arg(
                "start", now - dt.timedelta(days=self.dashboard["window_days"]))
            end = self._datetime_arg("end", now)
        if start >= end:
            raise ValueError("start 必须早于 end")
        min_score = (
            self.dashboard["min_score"] if fixed
            else self._bounded_float("min_score", self.dashboard["min_score"], 0, 10)
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
                subsystem=ctx.name,
                entity_resolver=self.entity_resolution_service,
                entity_registry=self.entity_alias_service,
            )
            self._engines[key] = engine
        return engine, query

    @staticmethod
    def _resolution_refs(data):
        raw_refs = data.get("refs")
        if raw_refs is None:
            raw_refs = [data.get("ref") or {}]
        if not isinstance(raw_refs, list):
            raise ResolutionError("refs 必须是情报实体引用数组")
        if len(raw_refs) > EntityResolutionService.MAX_BATCH_SIZE:
            raise ResolutionError(
                f"单次最多处理 {EntityResolutionService.MAX_BATCH_SIZE} 个情报实体引用")
        return [EntityRef.from_mapping(value or {}) for value in raw_refs]

    @staticmethod
    def _resolution_error(exc):
        status = {"not_found": 404, "conflict": 409}.get(exc.code, 400)
        return jsonify({"error": str(exc), "code": exc.code}), status

    @staticmethod
    def _json_safe(value):
        if isinstance(value, dt.datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: SituationViewsService._json_safe(item)
                    for key, item in value.items() if key != "_id"}
        if isinstance(value, list):
            return [SituationViewsService._json_safe(item) for item in value]
        return value

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

    @staticmethod
    def _entity_args(maximum=8):
        values = list(dict.fromkeys(
            str(value or "").strip() for value in request.args.getlist("entity")
            if str(value or "").strip()
        ))
        if not values:
            raise ValueError("至少需要一个 entity 参数")
        if len(values) > maximum:
            raise ValueError(f"一次最多选择 {maximum} 个实体")
        if any(len(value) > 300 for value in values):
            raise ValueError("entity 参数过长")
        return values
