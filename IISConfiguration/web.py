"""IIS 配置 Blueprint；不初始化 MongoDB、AI Client 或其他业务设施。"""

from __future__ import annotations

import secrets
import shlex
from copy import deepcopy
from typing import Callable, Optional

from flask import (
    Blueprint, Flask, jsonify, redirect, render_template, request, session,
    url_for,
)
from flask_babel import gettext

from WebI18n import ensure_web_i18n

from .admin_secret import AdminSecretError, AdminSecretService
from .service import IISConfigurationService


class IISConfigurationWeb:
    def __init__(self, service: IISConfigurationService, *,
                 setup_ai_url: str | None = None,
                 admin_secret_service: AdminSecretService | None = None):
        self.service = service
        self.setup_ai_url = setup_ai_url
        self.admin_secret_service = admin_secret_service or AdminSecretService()
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

        def localized_schema(schema: dict) -> dict:
            """只翻译 JSON Schema 的界面元数据，不改变校验契约。"""

            localized = deepcopy(schema)

            def visit(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in {"title", "description"} and isinstance(item, str):
                            value[key] = gettext(item)
                        else:
                            visit(item)
                elif isinstance(value, list):
                    for item in value:
                        visit(item)

            visit(localized)
            return localized

        def admin_secret_error(code: str) -> str:
            messages = {
                "invalid_username": gettext(
                    "Administrator username must be 3-50 characters using letters, "
                    "numbers, underscores, or hyphens."),
                "password_required": gettext("Administrator password is required."),
                "password_mismatch": gettext(
                    "The administrator passwords do not match."),
                "password_length": gettext(
                    "Administrator password must be 12-1024 characters."),
                "password_multiline": gettext(
                    "Administrator password must be a single line."),
                "invalid_secret_directory": gettext(
                    "Administrator secret directory is invalid."),
                "secret_directory_owner": gettext(
                    "Administrator secret directory is owned by another user."),
                "secret_directory_permissions": gettext(
                    "Administrator secret directory permissions must be 0700."),
                "authentication_exists": gettext(
                    "An authentication database already exists; use the administrator "
                    "management command instead."),
            }
            return messages.get(code, gettext(
                "Could not prepare the administrator credential."))

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
            initial_admin_required = not self.service.config_path.with_name(
                "Authentication.db").is_file()
            return render_template(
                "iis_setup.html",
                csrf_token=token,
                configuration_url=url_for("iis_configuration.page", advanced=1),
                ai_configuration_url=self.setup_ai_url,
                first_run=not self.service.config_path.is_file(),
                initial_admin_required=initial_admin_required,
                completion_message=(
                    gettext(
                        "Configuration and the temporary administrator credential "
                        "are ready. Configure AI clients, then run the unattended "
                        "installation command below.")
                    if initial_admin_required else gettext(
                        "Configuration is ready. Configure AI clients, then run the "
                        "unattended installation command below.")),
                config_api_url=url_for("iis_configuration.get_config"),
                validate_api_url=url_for("iis_configuration.validate"),
                save_api_url=url_for("iis_configuration.save"),
                admin_secret_api_url=url_for(
                    "iis_configuration.create_admin_secret"),
                install_command=(
                    "sudo deploy/iisctl install --non-interactive "
                    "--profile core,crawler "
                    f"--config {shlex.quote(str(self.service.config_path))} "
                    f"--ai-config {shlex.quote(str(self.service.config_path.with_name('ai_clients.yaml')))}"
                ),
            )

        @bp.get("/api/config")
        @protected
        def get_config():
            try:
                payload = self.service.read()
                payload["schema"] = localized_schema(payload["schema"])
                return jsonify(payload)
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

        @bp.post("/api/admin-secret")
        @protected
        def create_admin_secret():
            denied = require_csrf()
            if denied:
                return denied
            try:
                body = request.get_json(silent=True) or {}
                if self.service.config_path.with_name("Authentication.db").is_file():
                    raise AdminSecretError("authentication_exists")
                result = self.admin_secret_service.create(
                    body.get("username", ""),
                    body.get("password", ""),
                    body.get("confirmation", ""),
                )
                arguments = (
                    f"--admin-user {shlex.quote(result['username'])} "
                    f"--admin-password-file "
                    f"{shlex.quote(result['password_file'])} "
                    "--consume-admin-password-file"
                )
                return jsonify({
                    **result, "created": True,
                    "install_arguments": arguments,
                })
            except AdminSecretError as exc:
                return jsonify({
                    "created": False,
                    "error": admin_secret_error(exc.code),
                    "error_code": exc.code,
                }), 400
            except OSError:
                return jsonify({
                    "created": False,
                    "error": admin_secret_error("io_error"),
                    "error_code": "io_error",
                }), 400

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
