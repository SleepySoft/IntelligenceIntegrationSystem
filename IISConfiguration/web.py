"""IIS 配置 Blueprint；不初始化 MongoDB、AI Client 或其他业务设施。"""

from __future__ import annotations

import secrets
import shlex
from typing import Callable, Optional

from flask import (
    Blueprint, Flask, jsonify, redirect, render_template, request, session,
    url_for,
)

from WebI18n import ensure_web_i18n

from .service import IISConfigurationService


class IISConfigurationWeb:
    def __init__(self, service: IISConfigurationService, *,
                 setup_ai_url: str | None = None):
        self.service = service
        self.setup_ai_url = setup_ai_url
        self._registered_apps: set[int] = set()

    def create_blueprint(self, wrapper: Optional[Callable] = None) -> Blueprint:
        bp = Blueprint("iis_configuration", __name__, template_folder="templates")

        def protected(function):
            return wrapper(function) if wrapper else function

        def require_csrf():
            expected = session.get("iis_config_csrf")
            supplied = request.headers.get("X-Config-CSRF")
            if not expected or not secrets.compare_digest(expected, supplied or ""):
                return jsonify({"error": "Invalid or missing CSRF token."}), 403
            return None

        @bp.get("/")
        @protected
        def page():
            if (not self.service.config_path.is_file()
                    and request.args.get("advanced") != "1"):
                return redirect(url_for("iis_configuration.setup_page"))
            token = secrets.token_urlsafe(32)
            session["iis_config_csrf"] = token
            return render_template("iis_configuration.html", csrf_token=token)

        @bp.get("/setup/")
        @protected
        def setup_page():
            token = secrets.token_urlsafe(32)
            session["iis_config_csrf"] = token
            return render_template(
                "iis_setup.html",
                csrf_token=token,
                configuration_url=url_for("iis_configuration.page", advanced=1),
                ai_configuration_url=self.setup_ai_url,
                first_run=not self.service.config_path.is_file(),
                config_api_url=url_for("iis_configuration.get_config"),
                validate_api_url=url_for("iis_configuration.validate"),
                save_api_url=url_for("iis_configuration.save"),
                install_command=(
                    "sudo deploy/iisctl install --non-interactive "
                    f"--config {shlex.quote(str(self.service.config_path))} "
                    f"--ai-config {shlex.quote(str(self.service.config_path.with_name('ai_clients.yaml')))} "
                    "--admin-user admin --admin-password-file /path/to/admin-password"
                ),
            )

        @bp.get("/api/config")
        @protected
        def get_config():
            try:
                return jsonify(self.service.read())
            except (OSError, ValueError) as exc:
                return jsonify({"error": str(exc)}), 400

        @bp.get("/api/history")
        @protected
        def history():
            return jsonify({"items": self.service.history()})

        @bp.post("/api/validate")
        @protected
        def validate():
            denied = require_csrf()
            if denied:
                return denied
            try:
                body = request.get_json(silent=True) or {}
                result = (self.service.validate_text(body["yaml"])
                          if "yaml" in body else self.service.validate_data(body["data"]))
                return jsonify(result)
            except (KeyError, OSError, TypeError, ValueError) as exc:
                return jsonify({"valid": False, "error": str(exc)}), 400

        @bp.post("/api/save")
        @protected
        def save():
            denied = require_csrf()
            if denied:
                return denied
            try:
                body = request.get_json(silent=True) or {}
                result = (self.service.save_text(body["yaml"])
                          if "yaml" in body else self.service.save_data(body["data"]))
                return jsonify(result)
            except (KeyError, OSError, TypeError, ValueError) as exc:
                return jsonify({"saved": False, "error": str(exc)}), 400

        @bp.post("/api/restore")
        @protected
        def restore():
            denied = require_csrf()
            if denied:
                return denied
            try:
                result = self.service.restore((request.get_json(silent=True) or {})["name"])
                return jsonify(result)
            except (KeyError, OSError, ValueError) as exc:
                return jsonify({"restored": False, "error": str(exc)}), 400

        return bp

    def mount_to_app(self, app: Flask, *, url_prefix="/admin/config",
                     wrapper: Optional[Callable] = None) -> bool:
        identity = id(app)
        if identity in self._registered_apps:
            return False
        ensure_web_i18n(app)
        app.register_blueprint(self.create_blueprint(wrapper), url_prefix=url_prefix)
        self._registered_apps.add(identity)
        return True
