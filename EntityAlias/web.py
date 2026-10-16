# EntityAlias/web.py
# Flask Blueprint：实体别名 API。与 SituationViews 同一装配模式——
# Blueprint 在 mount_to_app 内创建，登录保护由注入的 wrapper 决定。

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from EntityAlias.model import AliasError
from EntityAlias.service import EntityAliasService

logger = logging.getLogger(__name__)

_ERROR_STATUS = {"bad_request": 400, "not_found": 404, "conflict": 409}


class EntityAliasWebService:
    """/entity-alias/ 命名空间的页面与 API。"""

    def __init__(self, service: EntityAliasService):
        self.service = service

    def mount_to_app(self, app, *, wrapper=None, url_prefix: str = "/entity-alias"):
        protect = wrapper or (lambda func: func)
        bp = Blueprint(
            "entity_alias", __name__,
            template_folder=str(Path(__file__).parent / "templates"),
        )
        service = self.service

        def _error_response(exc: AliasError):
            return jsonify({"error": str(exc), "code": exc.code}), \
                _ERROR_STATUS.get(exc.code, 400)

        # ------------------------------ 页面 ------------------------------

        @bp.route("/", methods=["GET"])
        def index_page():
            return render_template("entity_alias_status.html",
                                   url_prefix=url_prefix,
                                   status=service.status())

        # ------------------------------ 解析/查询 API ------------------------------

        @bp.route("/api/resolve", methods=["GET"])
        def api_resolve():
            name = request.args.get("name", "").strip()
            if not name:
                return jsonify({"error": "缺少 name 参数"}), 400
            return jsonify({"name": name, "found": service.resolve(name) is not None,
                            **(service.resolve(name) or {})})

        @bp.route("/api/resolve_batch", methods=["POST"])
        def api_resolve_batch():
            names = (request.get_json(silent=True) or {}).get("names", [])
            if not isinstance(names, list):
                return jsonify({"error": "names 必须是数组"}), 400
            return jsonify({"results": service.resolve_batch(names)})

        @bp.route("/api/lookup", methods=["GET"])
        def api_lookup():
            name = request.args.get("name", "").strip()
            if not name:
                return jsonify({"error": "缺少 name 参数"}), 400
            return jsonify(service.lookup(
                name,
                limit=int(request.args.get("limit", 5)),
                min_score=float(request.args.get("min_score", 0.4)),
                use_vector=request.args.get("vector", "1") not in ("0", "false"),
            ))

        @bp.route("/api/lookup_batch", methods=["POST"])
        def api_lookup_batch():
            data = request.get_json(silent=True) or {}
            names = data.get("names", [])
            if not isinstance(names, list):
                return jsonify({"error": "names 必须是数组"}), 400
            return jsonify({"results": service.lookup_batch(
                names,
                limit=int(data.get("limit", 5)),
                min_score=float(data.get("min_score", 0.4)),
                use_vector=bool(data.get("vector", True)),
            )})

        # ------------------------------ 状态 ------------------------------

        @bp.route("/api/status", methods=["GET"])
        def api_status():
            return jsonify(service.status())

        # ------------------------------ CRUD（写操作需登录保护） ------------------------------

        @bp.route("/api/entities", methods=["GET"])
        def api_list_entities():
            return jsonify(service.list_entities(
                type=request.args.get("type") or None,
                q=request.args.get("q", ""),
                limit=int(request.args.get("limit", 100)),
                offset=int(request.args.get("offset", 0)),
            ))

        @bp.route("/api/entities", methods=["POST"])
        @protect
        def api_create_entity():
            data = request.get_json(silent=True) or {}
            try:
                return jsonify(service.create_entity(
                    data.get("canonical", ""),
                    lang=data.get("lang", "zh"),
                    type=data.get("type", "other"),
                    aliases=data.get("aliases"),
                )), 201
            except AliasError as exc:
                return _error_response(exc)

        @bp.route("/api/entities/<entity_id>", methods=["GET"])
        def api_get_entity(entity_id):
            entity = service.get_entity(entity_id)
            if entity is None:
                return jsonify({"error": "实体不存在", "code": "not_found"}), 404
            return jsonify(entity)

        @bp.route("/api/entities/<entity_id>", methods=["PUT"])
        @protect
        def api_update_entity(entity_id):
            data = request.get_json(silent=True) or {}
            try:
                return jsonify(service.update_entity(entity_id, data))
            except AliasError as exc:
                return _error_response(exc)

        @bp.route("/api/entities/<entity_id>", methods=["DELETE"])
        @protect
        def api_delete_entity(entity_id):
            if not service.delete_entity(entity_id):
                return jsonify({"error": "实体不存在", "code": "not_found"}), 404
            return jsonify({"deleted": True})

        @bp.route("/api/entities/<entity_id>/aliases", methods=["POST"])
        @protect
        def api_add_alias(entity_id):
            data = request.get_json(silent=True) or {}
            try:
                return jsonify(service.add_alias(
                    entity_id, data.get("name", ""), data.get("lang", "")))
            except AliasError as exc:
                return _error_response(exc)

        @bp.route("/api/entities/<entity_id>/aliases", methods=["DELETE"])
        @protect
        def api_remove_alias(entity_id):
            data = request.get_json(silent=True) or {}
            try:
                return jsonify(service.remove_alias(entity_id, data.get("name", "")))
            except AliasError as exc:
                return _error_response(exc)

        @bp.route("/api/rebuild_vector", methods=["POST"])
        @protect
        def api_rebuild_vector():
            return jsonify({"synced_names": service.rebuild_vector()})

        app.register_blueprint(bp, url_prefix=url_prefix)
        logger.info("EntityAlias blueprint mounted at %s", url_prefix)
