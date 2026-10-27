import json
from pathlib import Path

from CrawlTasks.industry.crawler_config_manufacturingdive import CRAWLER_CONFIG
from IntelligenceCrawler.ProjectCatalog import scan_crawler_catalog
from IntelligenceCrawler.URLFilter import validate_url_filter_samples
from Scripts.deployment_preflight import resolve_collector_task_dirs


ROOT = Path(__file__).resolve().parent.parent


def test_manufacturing_dive_uses_narrow_rss_and_browser_only_for_articles():
    assert CRAWLER_CONFIG["entry_points"] == {
        "manufacturing_news": "https://www.manufacturingdive.com/feeds/news/",
    }
    assert CRAWLER_CONFIG["discoverer_name"] == "RSSDiscoverer"
    assert CRAWLER_CONFIG["d_fetcher_name"] == "RequestsFetcher"
    assert CRAWLER_CONFIG["e_fetcher_name"] == "PlaywrightFetcher"
    assert CRAWLER_CONFIG["extractor_name"] == "Trafilatura"


def test_manufacturing_dive_filter_accepts_news_and_rejects_sponsored_content():
    assert validate_url_filter_samples(CRAWLER_CONFIG) == []
    assert CRAWLER_CONFIG["url_filter"]["path_excludes"] == [
        "/spons/", "/press-release/",
    ]
    assert len(CRAWLER_CONFIG["url_filter_samples"]) >= 6


def test_manufacturing_dive_handlers_are_safe_for_committed_config():
    assert CRAWLER_CONFIG["article_filter"] is None
    assert CRAWLER_CONFIG["content_handler"] is None
    assert CRAWLER_CONFIG["exception_handler"] is None


def test_manufacturing_dive_task_is_catalogued_under_industry_domain():
    entries = scan_crawler_catalog(
        ROOT / "CrawlTasks",
        ROOT / "_config" / "collector_example.json",
    )
    entry = next(item for item in entries if item.name == "manufacturingdive")

    assert entry.group == "industry"
    assert entry.task_path is not None
    assert entry.task_path.name == "task_crawl_manufacturingdive.py"
    assert entry.discoverer == "RSSDiscoverer"
    assert entry.regression_sample_count == 6
    assert not entry.disabled


def test_collector_example_registers_industry_task_directory():
    payload = json.loads(
        (ROOT / "_config" / "collector_example.json").read_text(encoding="utf-8")
    )

    assert payload["collector"]["domain_task_dirs"]["industry"] == (
        "CrawlTasks/industry"
    )
    assert resolve_collector_task_dirs(payload["collector"])["industry"] == (
        "CrawlTasks/industry"
    )


def test_deployment_preflight_keeps_legacy_task_dirs_compatibility():
    assert resolve_collector_task_dirs({"task_dirs": {"news": "CrawlTasks/news"}}) == {
        "news": "CrawlTasks/news",
    }
