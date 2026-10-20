"""批量分析子系统的 HTTP 端点（手动触发与任务状态）。"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request, session

logger = logging.getLogger(__name__)


def create_blueprint(service, wrapper=None) -> Blueprint:
    bp = Blueprint("batch_analysis", __name__)
    protect = wrapper or (lambda func: func)

    @bp.route("/api/control/batch_analysis", methods=["GET", "POST"])
    @protect
    def batch_analysis_api():
        """POST 提交积压；GET 查任务状态。"""
        if request.method == "GET":
            return jsonify({"jobs": service.list_jobs()})
        body = request.get_json(silent=True) or {}
        operator = session.get("username", "")
        try:
            result = service.submit_backlog(
                lookback_days=body.get("lookback_days"),
                limit=body.get("limit"))
        except Exception as exc:
            logger.warning("批量分析提交失败: %s", exc, exc_info=True)
            return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
        logger.warning("运行控制：提交批量分析（操作人: %s），结果: %s",
                       operator or "unknown", result)
        return jsonify(result)

    return bp
