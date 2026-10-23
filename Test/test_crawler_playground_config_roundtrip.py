import datetime

from CrawlTasks.news.crawler_config_cna import _is_relevant_article
from CrawlTasks.news.crawler_config_euronews import CRAWLER_CONFIG as EURONEWS_CONFIG
from IntelligenceCrawler.CrawlerCodeGenerator import CrawlerCodeGenerator
from IntelligenceCrawler.PlaygroundConfigState import PlaygroundConfigState


def _loaded_config():
    return {
        "entry_points": {"asia": "https://example.test/asia.xml"},
        "discoverer_name": "RSSDiscoverer",
        "discoverer_init_param": {"verbose": False, "extension_option": "keep"},
        "d_fetcher_name": "RequestsFetcher",
        "d_fetcher_init_param": {"timeout_s": 20, "extension_option": "keep"},
        "d_fetcher_kwargs": {"wait_until": "networkidle"},
        "extractor_name": "Trafilatura",
        "extractor_init_param": {"verbose": False},
        "extractor_kwargs": {"extension_option": "keep"},
        "article_filter": _is_relevant_article,
        "content_handler": None,
        "exception_handler": None,
        "period_filter": (
            datetime.datetime(2026, 10, 1),
            datetime.datetime(2026, 10, 2),
        ),
    }


def _ui_projection():
    return {
        "entry_points": {"asia": "https://example.test/asia.xml"},
        "discoverer_name": "RSSDiscoverer",
        "discoverer_init_param": {"verbose": True},
        "d_fetcher_name": "RequestsFetcher",
        "d_fetcher_init_param": {"timeout_s": 20, "proxy": None},
        "d_fetcher_kwargs": {"wait_until": "networkidle"},
        "extractor_name": "Trafilatura",
        "extractor_init_param": {"verbose": True},
        "extractor_kwargs": {},
        "channel_filter": None,
        "date_filter_enabled": False,
        "date_filter_days": 7,
        "article_filter": None,
        "content_handler": None,
        "exception_handler": None,
    }


def test_loaded_config_round_trip_preserves_non_ui_values():
    original = _loaded_config()
    projection = _ui_projection()
    state = PlaygroundConfigState()
    state.capture(original, projection)

    rebuilt = state.merge(projection)

    assert rebuilt == original
    assert rebuilt["article_filter"] is _is_relevant_article


def test_editing_one_ui_field_only_changes_that_field():
    original = _loaded_config()
    projection = _ui_projection()
    state = PlaygroundConfigState()
    state.capture(original, projection)
    edited = _ui_projection()
    edited["d_fetcher_init_param"]["timeout_s"] = 35

    rebuilt = state.merge(edited)

    assert rebuilt["d_fetcher_init_param"] == {
        "timeout_s": 35,
        "extension_option": "keep",
    }
    assert rebuilt["discoverer_init_param"]["extension_option"] == "keep"
    assert rebuilt["extractor_kwargs"]["extension_option"] == "keep"
    assert rebuilt["period_filter"] == original["period_filter"]
    assert rebuilt["article_filter"] is _is_relevant_article


def test_switching_component_drops_incompatible_hidden_parameters():
    state = PlaygroundConfigState()
    state.capture(_loaded_config(), _ui_projection())
    edited = _ui_projection()
    edited["d_fetcher_name"] = "PlaywrightFetcher"
    edited["d_fetcher_init_param"] = {"timeout_s": 20, "stealth": True}
    edited["d_fetcher_kwargs"] = {"wait_until": "domcontentloaded"}

    rebuilt = state.merge(edited)

    assert rebuilt["d_fetcher_init_param"] == {"timeout_s": 20, "stealth": True}
    assert "extension_option" not in rebuilt["d_fetcher_init_param"]


def test_generator_keeps_module_level_article_filter_executable():
    code = CrawlerCodeGenerator().generate_code_from_config(_loaded_config())
    namespace = {}

    exec(compile(code, "<generated-crawler-config>", "exec"), namespace)

    generated_filter = namespace["CRAWLER_CONFIG"]["article_filter"]
    assert generated_filter("https://www.channelnewsasia.com/asia/example")
    assert not generated_filter("https://www.channelnewsasia.com/sport/example")


def test_generator_keeps_existing_lambda_filter_executable():
    code = CrawlerCodeGenerator().generate_code_from_config(EURONEWS_CONFIG)
    namespace = {}

    exec(compile(code, "<generated-euronews-config>", "exec"), namespace)

    generated_filter = namespace["CRAWLER_CONFIG"]["article_filter"]
    assert generated_filter("https://example.test/news/story")
    assert not generated_filter("https://example.test/video/story")
