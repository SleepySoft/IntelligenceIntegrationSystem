# VectorIntelligence/web.py
# 向量子系统的 /vector/ Blueprint：状态、搜索、聚类、动态图谱页面与 API。
#
# 装配模式与 SituationViews 一致：Blueprint 在 mount_to_app 内创建，
# 登录保护通过注入的 wrapper 应用；所有 Core 能力经构造参数注入
# （hub 门面、hub_service 查询流程、doc_cleaner 投影），不 import Core Web 模块。
#
# 兼容说明：旧入口（/intelligences/query?search_mode=vector_*、/api/clusters/*、
# /intelligence/graph/* 等）仍由 Core Web 提供并指向同一批引擎，待确认无旧调用后下线。

import logging
from pathlib import Path

import dateutil.parser
from flask import Blueprint, jsonify, render_template, request, session

from ServiceComponent.intelligence_projections import IntelligenceProjectionProfile

logger = logging.getLogger(__name__)


class VectorWebService:
    """/vector/ 命名空间的页面与 API 集合。"""

    def __init__(self, *, hub, hub_service, doc_cleaner, handles=None):
        self.hub = hub
        self.hub_service = hub_service
        self.doc_cleaner = doc_cleaner
        self.handles = handles

    # ------------------------------------------------------------------ 内部辅助

    def _extension(self):
        return self.handles.extension if self.handles else None

    def _search_with_params(self, params):
        """复用 Core 已并轨的查询流程（限流/公开限制/性能日志保持单份实现）。"""
        if not str(params.get('search_mode', '')).startswith('vector'):
            params['search_mode'] = 'vector_text'
        return self.hub_service._query_api_with_params(params, None)

    # ------------------------------------------------------------------ 装配

    def mount_to_app(self, app, *, wrapper=None, url_prefix='/vector'):
        protect = wrapper or (lambda func: func)
        package_dir = Path(__file__).parent
        bp = Blueprint(
            'vector_intelligence', __name__,
            template_folder=str(package_dir / 'templates'),
            static_folder=str(package_dir / 'static'),
            static_url_path='/static',
        )
        hub = self.hub
        hub_service = self.hub_service
        doc_cleaner = self.doc_cleaner

        # ------------------------------ 页面 ------------------------------

        @bp.route('/', methods=['GET'])
        def status_page():
            extension = self._extension()
            return render_template(
                'vector_status.html',
                vector_base=url_prefix,
                vector_ready=bool(extension and extension.ready),
            )

        @bp.route('/search', methods=['GET'])
        def search_page():
            # 复用 Core 搜索页模板（向量 tab 已在其中）；旧“相似情报”深链仍由
            # Core 列表页承接（/intelligences?search_mode=vector_similar&...）。
            is_public = not session.get('logged_in', False)
            return render_template(
                'intelligence_search.html',
                public_mode=is_public,
                public_limits=hub_service.public_search_limits if is_public else {},
                vector_enabled=True,  # 本蓝图仅在向量已配置时挂载
                **hub_service._default_subsystem_plugin_params()
            )

        @bp.route('/clusters', methods=['GET'])
        def clusters_page():
            return render_template('vector_cluster_list.html', vector_base=url_prefix)

        @bp.route('/graph', methods=['GET'])
        @protect
        def graph_page():
            seed_uuid = request.args.get('uuid', '') or request.args.get('seed_uuid', '')
            return render_template('vector_graph.html', seed_uuid=seed_uuid, vector_base=url_prefix)

        # ------------------------------ API ------------------------------

        @bp.route('/api/status', methods=['GET'])
        def api_status():
            extension = self._extension()
            if extension is None:
                return jsonify({"enabled": False, "ready": False}), 503
            return jsonify({
                "enabled": True,
                "ready": extension.ready,
                "summary_engine": extension.summary_engine is not None,
                "full_text_engine": extension.full_text_engine is not None,
            })

        @bp.route('/api/search', methods=['GET', 'POST'])
        def api_search():
            params = hub_service._get_combined_params()
            # 支持 mode=text|similar 别名（映射到 search_mode=vector_text/vector_similar）。
            alias = (request.values.get('mode') or '').strip().lower()
            if alias:
                params['search_mode'] = {
                    'text': 'vector_text',
                    'similar': 'vector_similar',
                }.get(alias, params['search_mode'])
            return self._search_with_params(params)

        @bp.route('/api/similar/<string:reference_uuid>', methods=['GET'])
        def api_similar(reference_uuid):
            params = hub_service._get_combined_params()
            params['search_mode'] = 'vector_similar'
            params['reference'] = reference_uuid
            return self._search_with_params(params)

        @bp.route('/api/clusters/latest', methods=['GET'])
        def api_clusters_latest():
            try:
                limit = int(request.args.get("limit", 200))
                desc = request.args.get("desc", "1") in ("1", "true", "True", "yes", "Y", "y")
                source = request.args.get("source", "online").lower().strip()

                agg = hub.get_service("aggregation")
                if not agg:
                    return jsonify({"error": "Aggregation engine not configured"}), 501

                def doc_fetcher(uuids):
                    return hub.get_intelligence(
                        uuids, profile=IntelligenceProjectionProfile.CARD)

                result = agg.build_rich_clusters_latest(
                    doc_fetcher=doc_fetcher,
                    doc_cleaner=doc_cleaner,
                    limit=limit,
                    descending=desc,
                    source=source,
                )
                return jsonify(result), 200
            except Exception as e:
                logger.exception("vector api_clusters_latest error")
                return jsonify({"error": str(e)}), 500

        @bp.route('/api/clusters/<cluster_id>/members', methods=['GET'])
        def api_cluster_members(cluster_id: str):
            try:
                limit = int(request.args.get("limit", 100))
                offset = int(request.args.get("offset", 0))
                sort_by = request.args.get("sort_by", "time")
                desc = request.args.get("desc", "1") in ("1", "true", "True")
                source = request.args.get("source", "online").lower().strip()

                agg = hub.get_service("aggregation")
                if not agg:
                    return jsonify({"error": "Aggregation engine not configured"}), 501

                def doc_fetcher(uuids):
                    return hub.get_intelligence(
                        uuids, profile=IntelligenceProjectionProfile.CARD)

                result = agg.build_rich_cluster_members(
                    cluster_id=cluster_id,
                    doc_fetcher=doc_fetcher,
                    doc_cleaner=doc_cleaner,
                    offset=offset,
                    limit=limit,
                    sort_by=sort_by,
                    descending=desc,
                    source=source,
                )
                result["source"] = source
                return jsonify(result), 200
            except ValueError as ve:
                return jsonify({"error": str(ve)}), 404
            except Exception as e:
                logger.exception("vector api_cluster_members error")
                return jsonify({"error": str(e)}), 500

        @bp.route('/api/debug/trigger_aggregation', methods=['POST'])
        @protect
        def api_trigger_aggregation():
            data = request.get_json() or {}
            start_str = data.get('start_time')
            end_str = data.get('end_time')

            time_range = None
            if start_str and end_str:
                time_range = (dateutil.parser.parse(start_str).timestamp(),
                              dateutil.parser.parse(end_str).timestamp())

            try:
                agg_engine = hub.get_service("aggregation")
                if not agg_engine:
                    return jsonify({"error": "Engine not ready"}), 503

                def doc_fetcher(uuids):
                    return hub.get_intelligence(
                        uuids, profile=IntelligenceProjectionProfile.CARD)

                job_id = agg_engine.trigger_offline(time_range=time_range, doc_fetcher=doc_fetcher)
                return jsonify({
                    "status": "success",
                    "job_id": job_id,
                    "mode": "explicit_time" if time_range else "auto_discovery",
                })
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @bp.route('/api/graph/build', methods=['POST'])
        @protect
        def api_graph_build():
            graph_engine = hub.get_service("dynamic_graph")
            if graph_engine is None:
                return jsonify({
                    "error": "Service Unavailable",
                    "message": "Graph engine is not ready yet.",
                    "code": "ENGINE_INITIALIZING",
                }), 503

            data = request.get_json() or {}
            seed_uuid = data.get('seed_uuid')
            if not seed_uuid:
                return jsonify({"error": "Missing seed_uuid"}), 400

            try:
                thread_id = graph_engine.trigger_graph_build(
                    seed_uuid=seed_uuid,
                    max_depth=int(data.get('max_depth', 3)),
                    window_days=int(data.get('window_days', 7)),
                )
                return jsonify({
                    "status": "success",
                    "thread_id": thread_id,
                    "message": "Graph building task submitted.",
                }), 202
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @bp.route('/api/graph/status/<thread_id>', methods=['GET'])
        @protect
        def api_graph_status(thread_id):
            graph_engine = hub.get_service("dynamic_graph")
            if graph_engine is None:
                return jsonify({
                    "error": "Service Unavailable",
                    "message": "Graph engine is not ready yet.",
                    "code": "ENGINE_INITIALIZING",
                }), 503

            snapshot = graph_engine.get_snapshot(thread_id)
            if not snapshot:
                return jsonify({"error": "Thread ID not found"}), 404

            return jsonify({
                "thread_id": snapshot.get("thread_id"),
                "status": snapshot.get("status"),
                "created_at": snapshot.get("created_at"),
                "nodes": snapshot.get("nodes", []),
                "edges": snapshot.get("edges", []),
                "llm_summary": snapshot.get("llm_summary"),
                "llm_evaluation_score": snapshot.get("llm_evaluation_score"),
            })

        app.register_blueprint(bp, url_prefix=url_prefix)
        logger.info("VectorIntelligence blueprint mounted at %s", url_prefix)
