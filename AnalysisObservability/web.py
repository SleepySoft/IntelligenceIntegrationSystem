"""可独立挂载的 Flask 统计页面。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from flask import Blueprint, jsonify, render_template, request

from .report import AnalysisReportService, DIMENSIONS


def create_blueprint(
    *, analysis_db_path: str | Path, call_db_path: str | Path,
    wrapper: Callable | None = None, name: str = "analysis_observability",
) -> Blueprint:
    """创建无 IIS 依赖的 Blueprint；wrapper 可注入宿主认证装饰器。"""
    blueprint = Blueprint(name, __name__, template_folder="templates")
    service = AnalysisReportService(analysis_db_path, call_db_path)

    def protected(func):
        return wrapper(func) if wrapper else func

    @blueprint.get("/")
    @protected
    def index():
        return render_template(
            "analysis_observability.html", embedded=request.args.get("embed") == "1")

    @blueprint.get("/api/report")
    @protected
    def api_report():
        try:
            hours = int(request.args.get("hours", "24"))
            dimension = request.args.get("dimension", "model")
            include_shadow = request.args.get("include_shadow", "0") == "1"
            if dimension not in DIMENSIONS:
                raise ValueError("无效的 dimension")
            return jsonify(service.report(
                hours=hours, dimension=dimension, include_shadow=include_shadow))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    return blueprint
