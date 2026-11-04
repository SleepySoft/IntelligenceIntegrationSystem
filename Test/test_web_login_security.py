from IntelligenceHubWebService import IntelligenceHubWebService, WebServiceAccessManager
from flask import Flask
from PyLoggingBackend.LoggerBackend import LoggerBackend


def test_login_next_accepts_only_local_absolute_paths():
    safe = IntelligenceHubWebService._safe_local_redirect_target

    assert safe("/ops/traffic/") == "/ops/traffic/"
    assert safe("/ops/traffic/?window=7") == "/ops/traffic/?window=7"
    assert safe("https://evil.example/") is None
    assert safe("//evil.example/") is None
    assert safe("/\\evil.example/") is None
    assert safe("/ops/traffic/\r\nLocation: https://evil.example") is None
    assert safe(None) is None


def test_login_required_preserves_full_local_url_for_refresh_and_reentry():
    app = Flask(__name__)
    app.secret_key = "test"

    @app.get("/login")
    def login():
        return "login"

    @app.get("/industry/shadow/compare/<uuid>")
    @WebServiceAccessManager.login_required
    def compare(uuid):
        return uuid

    response = app.test_client().get(
        "/industry/shadow/compare/u-1?left=primary&right=shadow&prompt_diff=1")

    assert response.status_code == 302
    assert response.location.endswith(
        "/login?next=/industry/shadow/compare/u-1?left%3Dprimary%26right%3Dshadow%26prompt_diff%3D1")


def test_logger_routes_do_not_replace_host_session_secret(tmp_path):
    app = Flask(__name__)
    app.secret_key = "stable-host-secret"
    backend = LoggerBackend(str(tmp_path / "iis.log"), 10)

    assert backend.register_router(app) is True
    assert app.secret_key == "stable-host-secret"
