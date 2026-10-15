import json
import re
from functools import wraps
from urllib.parse import parse_qs, urlparse

from flask import Flask

from IntelligenceViews.iis_integration import mount_to_iis
from IntelligenceViews.web import IntelligenceViewsService


SUBSYSTEMS = [
    {
        "name": "news",
        "display_name": "国际新闻",
        "is_default": True,
        "url_prefix": "",
        "enabled": True,
    },
    {
        "name": "finance",
        "display_name": "财经情报",
        "is_default": False,
        "url_prefix": "/finance",
        "enabled": True,
    },
]


def _frame_urls(html):
    matched = re.search(r"const FRAME_URLS = (\{.*?\});", html)
    assert matched
    return json.loads(matched.group(1))


def test_integration_page_composes_three_existing_pages_with_shared_scope():
    app = Flask(__name__)
    IntelligenceViewsService(SUBSYSTEMS).mount_to_app(app, url_prefix="/views")
    response = app.test_client().get("/views/", query_string={
        "subsystem": "finance",
        "start": "2026-10-01T00:00:00Z",
        "end": "2026-10-15T00:00:00Z",
        "min_score": "7.5",
        "view": "map",
    })

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "情报列表" in html
    assert "地域视图" in html
    assert "实体视图" in html
    assert 'let activeView = "map"' in html

    urls = _frame_urls(html)
    assert urlparse(urls["list"]).path == "/finance/intelligences"
    assert parse_qs(urlparse(urls["list"]).query)["threshold"] == ["7.5"]
    map_query = parse_qs(urlparse(urls["map"]).query)
    assert urlparse(urls["map"]).path == "/situation/embed/map"
    assert map_query["subsystem"] == ["finance"]
    assert map_query["start"] == ["2026-10-01T00:00:00+00:00"]
    assert map_query["end"] == ["2026-10-15T00:00:00+00:00"]
    assert map_query["min_score"] == ["7.5"]
    assert urlparse(urls["entities"]).path == "/situation/embed/entities"


def test_integration_page_rejects_unknown_subsystem_and_invalid_range():
    app = Flask(__name__)
    IntelligenceViewsService(SUBSYSTEMS).mount_to_app(app, url_prefix="/views")
    client = app.test_client()

    assert client.get("/views/?subsystem=missing").status_code == 400
    assert client.get(
        "/views/?start=2026-10-15T00:00:00Z&end=2026-10-01T00:00:00Z"
    ).status_code == 400


def test_iis_mount_is_optional_and_uses_supplied_auth_wrapper():
    class Registry:
        @staticmethod
        def describe():
            return SUBSYSTEMS

    class EnabledConfig:
        @staticmethod
        def get(key, default=None):
            if key == "intelligence_hub_web_service.intelligence_views":
                return {"enabled": True, "url_prefix": "/insight"}
            return default

    def require_login(func):
        @wraps(func)
        def protected(*args, **kwargs):
            return "login required", 401
        return protected

    app = Flask(__name__)
    service = mount_to_iis(
        app,
        config=EnabledConfig(),
        subsystem_registry=Registry(),
        wrapper=require_login,
    )
    assert service is not None
    assert app.config["INTELLIGENCE_VIEWS_ENABLED"] is True
    assert app.test_client().get("/insight/").status_code == 401

    class DisabledConfig:
        @staticmethod
        def get(key, default=None):
            if key == "intelligence_hub_web_service.intelligence_views":
                return {"enabled": False}
            return default

    disabled_app = Flask(__name__)
    service = mount_to_iis(
        disabled_app,
        config=DisabledConfig(),
        subsystem_registry=Registry(),
    )
    assert service is None
    assert disabled_app.config["INTELLIGENCE_VIEWS_ENABLED"] is False
    assert disabled_app.test_client().get("/views/").status_code == 404
