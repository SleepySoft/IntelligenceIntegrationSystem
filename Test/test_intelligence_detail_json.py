from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from flask import Flask

from IntelligenceHubWebService import IntelligenceHubWebService
from ServiceComponent.intelligence_projections import IntelligenceProjectionProfile


class _Hub:
    def __init__(self, document):
        self.document = document
        self.calls = []

    def get_intelligence(self, intelligence_uuid, domain_name=None, profile=None):
        self.calls.append((intelligence_uuid, domain_name, profile))
        return self.document


class _Context:
    name = "news"
    is_default = True
    ui_plugin_enabled = False
    config_dir = None


def test_intelligence_detail_api_returns_public_v4_shape_without_storage_metadata():
    event_id = uuid4()
    hub = _Hub({
        "UUID": "intelligence-id",
        "EVENT_TITLE": "示例事件",
        "events": [{"event_uuid": event_id}],
        "archived_at": datetime(2026, 10, 10, tzinfo=timezone.utc),
    })
    service = IntelligenceHubWebService.__new__(IntelligenceHubWebService)
    service.intelligence_hub = hub

    app = Flask(__name__)
    app.register_blueprint(
        service._build_domain_blueprint(_Context(), url_prefix="/news"),
        url_prefix="/news",
    )

    response = app.test_client().get("/news/api/intelligence/intelligence-id")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["UUID"] == "intelligence-id"
    assert payload["data"]["events"][0]["event_uuid"] == str(event_id)
    assert "_id" not in payload["data"]
    assert "APPENDIX" not in payload["data"]
    assert payload["data"]["archived_at"] == "2026-10-10T00:00:00+00:00"
    assert hub.calls == [(
        "intelligence-id", "news", IntelligenceProjectionProfile.DETAIL)]


def test_domain_blueprint_exposes_domain_name_to_page():
    service = IntelligenceHubWebService.__new__(IntelligenceHubWebService)
    service.intelligence_hub = SimpleNamespace(vector_search_available=False)
    service.public_search_limits = {}

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parents[1] / "templates"),
    )
    app.jinja_env.globals["_"] = lambda value: value
    app.register_blueprint(
        service._build_domain_blueprint(_Context(), url_prefix="/news"),
        url_prefix="/news",
    )

    response = app.test_client().get("/news/intelligences")

    assert response.status_code == 200
    assert 'window.IIS_DOMAIN = "news";' in response.get_data(as_text=True)


def test_root_page_context_uses_configured_default_domain():
    context = SimpleNamespace(
        name="finance", url_prefix="/finance", ui_plugin_enabled=False,
    )
    service = IntelligenceHubWebService.__new__(IntelligenceHubWebService)
    service.intelligence_hub = SimpleNamespace(default_domain=context)

    params = service._default_domain_plugin_params()

    assert params["domain_name"] == "finance"
    assert params["base_path"] == ""
