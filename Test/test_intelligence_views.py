import json
import re
from functools import wraps
from urllib.parse import urlparse

from flask import Flask

from IntelligenceViews.iis_integration import mount_to_iis
from IntelligenceViews.web import IntelligenceViewsService


DOMAINS = [
    {
        "name": "news",
        "display_name": "国际新闻",
        "is_default": True,
        "url_prefix": "",
        "enabled": True,
    },
    {
        "name": "industry",
        "display_name": "行业情报",
        "is_default": False,
        "url_prefix": "/industry",
        "enabled": True,
    },
]


def _frame_urls(html):
    matched = re.search(r"const FRAME_URLS = (\{.*?\});", html)
    assert matched
    return json.loads(matched.group(1))


def test_integration_page_limits_only_situation_views_from_configuration():
    app = Flask(__name__)
    service = IntelligenceViewsService(DOMAINS, settings={
        "situation": {
            "domain_name": "industry",
            "window_days": 14,
            "min_score": 7.5,
        },
    })
    service.mount_to_app(app, url_prefix="/views")
    response = app.test_client().get("/views/", query_string={
        "domain": "news",
        "start": "2026-10-01T00:00:00Z",
        "end": "2026-10-15T00:00:00Z",
        "min_score": "1",
        "view": "map",
    })

    assert response.status_code == 200
    assert '<select id="language-select"' in response.get_data(as_text=True)
    assert '<option value="zh-CN" selected>' in response.get_data(as_text=True)
    html = response.get_data(as_text=True)
    assert "情报列表" in html
    assert "地域视图" in html
    assert "实体视图" in html
    assert "实体时间线" in html
    assert 'let activeView = "map"' in html
    assert service.situation == {
        "domain_name": "news",
        "domain_display_name": "国际新闻",
        "window_days": 14,
        "public_window_days": 14,
        "timeline_max_entities": 8,
        "public_timeline_max_entities": 8,
        "min_score": 7.5,
        "min_support": 2,
    }
    assert "情报视图</h1>" not in html
    assert "完整情报列表；筛选、排序与分页保持原页面行为。" not in html

    urls = _frame_urls(html)
    assert urls["list"] == "/intelligences?threshold=6"
    assert urls["map"] == "/news/situation/embed/map"
    assert urls["entities"] == "/news/situation/embed/entities"
    assert urls["timeline"] == "/news/situation/embed/timeline"
    assert not urlparse(urls["map"]).query
    assert not urlparse(urls["entities"]).query


def test_named_domain_views_bind_every_frame_to_the_domain_prefix():
    app = Flask(__name__)
    IntelligenceViewsService(DOMAINS).mount_to_app(app, url_prefix="/views")
    client = app.test_client()

    news_html = client.get("/news/views/").get_data(as_text=True)
    industry_html = client.get("/industry/views/").get_data(as_text=True)

    assert _frame_urls(news_html) == {
        "list": "/news/intelligences?threshold=6",
        "map": "/news/situation/embed/map",
        "entities": "/news/situation/embed/entities",
        "timeline": "/news/situation/embed/timeline",
    }
    assert _frame_urls(industry_html) == {
        "list": "/industry/intelligences?threshold=6",
        "map": "/industry/situation/embed/map",
        "entities": "/industry/situation/embed/entities",
        "timeline": "/industry/situation/embed/timeline",
    }
    assert "国际新闻" in news_html
    assert "行业情报" in industry_html
    assert '<option value="/news/views/">国际新闻</option>' in industry_html
    assert '<option value="/industry/views/" selected>行业情报</option>' in industry_html
    assert "target.search = current.search" in industry_html
    assert "const LIST_FRAME_PATH = new URL(FRAME_URLS.list" in industry_html
    assert "FRAME_URLS.list = `${LIST_FRAME_PATH}" in industry_html


def test_timeline_query_is_allowlisted_and_repeated_entities_are_preserved():
    app = Flask(__name__)
    IntelligenceViewsService(DOMAINS).mount_to_app(app, url_prefix="/views")
    response = app.test_client().get("/views/", query_string=[
        ("view", "timeline"),
        ("entity", "ent_us"),
        ("entity", "organization|US|white house"),
        ("mode", "lanes"),
        ("orientation", "vertical"),
        ("ignored", "no"),
    ])

    html = response.get_data(as_text=True)
    urls = _frame_urls(html)
    timeline = urlparse(urls["timeline"])
    assert timeline.path == "/news/situation/embed/timeline"
    assert "entity=ent_us" in timeline.query
    assert "entity=organization%7CUS%7Cwhite+house" in timeline.query
    assert "mode=lanes" in timeline.query
    assert "orientation=vertical" in timeline.query
    assert "ignored" not in timeline.query
    assert 'let activeView = "timeline"' in html
    assert "iis:timeline-state" in html
    assert "iis:open-timeline" in html


def test_public_timeline_limits_entities_and_logged_in_view_uses_full_scope():
    app = Flask(__name__)
    app.secret_key = "test"
    service = IntelligenceViewsService(DOMAINS, settings={
        "public_access": True,
        "situation": {
            "window_days": 14,
            "public_window_days": 5,
            "timeline_max_entities": 8,
            "public_timeline_max_entities": 2,
        },
    })
    service.mount_to_app(app, url_prefix="/views")
    client = app.test_client()
    query = [("view", "timeline"), ("entity", "ent_a"),
             ("entity", "ent_b"), ("entity", "ent_c")]

    public_html = client.get("/views/", query_string=query).get_data(as_text=True)
    public_urls = _frame_urls(public_html)
    assert "entity=ent_a" in public_urls["timeline"]
    assert "entity=ent_b" in public_urls["timeline"]
    assert "entity=ent_c" not in public_urls["timeline"]
    assert '"timeline_max_entities": 2' in public_html
    assert '"window_days": 5' in public_html
    assert 'data-viewer-session' not in public_html

    with client.session_transaction() as values:
        values["logged_in"] = True
        values["username"] = "operator"
    logged_html = client.get("/views/", query_string=query).get_data(as_text=True)
    logged_urls = _frame_urls(logged_html)
    assert "entity=ent_c" in logged_urls["timeline"]
    assert '"timeline_max_entities": 8' in logged_html
    assert '"window_days": 14' in logged_html
    assert 'data-viewer-session' in logged_html
    assert "已登录" in logged_html
    assert "operator" in logged_html
    assert 'href="/logout"' in logged_html
    assert "退出登录" in logged_html


def test_integration_page_ignores_scope_query_and_falls_back_for_unknown_view():
    app = Flask(__name__)
    IntelligenceViewsService(DOMAINS).mount_to_app(app, url_prefix="/views")
    client = app.test_client()

    response = client.get(
        "/views/?subsystem=missing&start=bad&end=bad&min_score=99&view=bad"
    )
    assert response.status_code == 200
    assert 'let activeView = "list"' in response.get_data(as_text=True)


def test_list_query_is_forwarded_and_parent_url_tracks_iframe_pagination():
    app = Flask(__name__)
    IntelligenceViewsService(DOMAINS).mount_to_app(app, url_prefix="/views")

    response = app.test_client().get("/views/", query_string={
        "view": "list",
        "page": "2",
        "per_page": "20",
        "threshold": "8",
        "sort_by": "content_time",
        "ignored": "must-not-reach-frame",
    })

    html = response.get_data(as_text=True)
    urls = _frame_urls(html)
    query = dict(item.split("=", 1) for item in urlparse(urls["list"]).query.split("&"))
    assert query == {
        "page": "2", "per_page": "20", "threshold": "8",
        "sort_by": "content_time",
    }
    assert "must-not-reach-frame" not in urls["list"]
    assert "event.data?.type !== 'iis:list-state'" in html
    assert "syncOuterListState(event.data.state, event.data.history_mode)" in html
    assert "window.addEventListener('popstate'" in html


def test_iis_mount_is_optional_and_uses_supplied_auth_wrapper():
    class Registry:
        @staticmethod
        def describe_domains():
            return DOMAINS

    class EnabledConfig:
        @staticmethod
        def get(key, default=None):
            if key == "intelligence_hub_web_service.intelligence_views":
                return {
                    "enabled": True,
                    "public_access": False,
                    "url_prefix": "/insight",
                }
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
        domain_resources=Registry(),
        wrapper=require_login,
    )
    assert service is not None
    assert app.config["INTELLIGENCE_VIEWS_ENABLED"] is True
    assert app.test_client().get("/insight/").status_code == 401
    assert app.test_client().get("/news/insight/").status_code == 401
    assert app.test_client().get("/industry/insight/").status_code == 401

    class PublicConfig:
        @staticmethod
        def get(key, default=None):
            if key == "intelligence_hub_web_service.intelligence_views":
                return {
                    "enabled": True,
                    "public_access": True,
                    "url_prefix": "/public-insight",
                }
            return default

    public_app = Flask(__name__)
    service = mount_to_iis(
        public_app,
        config=PublicConfig(),
        domain_resources=Registry(),
        wrapper=require_login,
    )
    assert service is not None
    assert public_app.test_client().get("/public-insight/").status_code == 200
    assert public_app.test_client().get("/news/public-insight/").status_code == 200
    assert public_app.test_client().get("/industry/public-insight/").status_code == 200

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
        domain_resources=Registry(),
    )
    assert service is None
    assert disabled_app.config["INTELLIGENCE_VIEWS_ENABLED"] is False
    assert disabled_app.test_client().get("/views/").status_code == 404
