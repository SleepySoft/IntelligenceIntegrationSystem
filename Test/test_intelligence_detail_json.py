from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from flask import Flask

from IntelligenceHubWebService import IntelligenceHubWebService


class _Hub:
    def __init__(self, document):
        self.document = document
        self.calls = []

    def get_intelligence(self, intelligence_uuid, subsystem=None):
        self.calls.append((intelligence_uuid, subsystem))
        return self.document


class _Context:
    name = "news"
    is_default = True
    ui_plugin_enabled = False
    config_dir = None


def test_intelligence_detail_api_serializes_nested_mongo_object_id():
    mongo_id = ObjectId()
    event_id = uuid4()
    hub = _Hub({
        "_id": "intelligence-id",
        "raw_data": {
            "_id": mongo_id,
            "nested": [{"event_uuid": event_id}],
        },
        "archived_at": datetime(2026, 10, 10, tzinfo=timezone.utc),
    })
    service = IntelligenceHubWebService.__new__(IntelligenceHubWebService)
    service.intelligence_hub = hub

    app = Flask(__name__)
    app.register_blueprint(
        service._build_subsystem_blueprint(_Context(), url_prefix="/news"),
        url_prefix="/news",
    )

    response = app.test_client().get("/news/api/intelligence/intelligence-id")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["raw_data"]["_id"] == str(mongo_id)
    assert payload["data"]["raw_data"]["nested"][0]["event_uuid"] == str(event_id)
    assert payload["data"]["archived_at"] == "2026-10-10T00:00:00+00:00"
    assert hub.calls == [("intelligence-id", "news")]
