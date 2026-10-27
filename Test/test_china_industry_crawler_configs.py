from pathlib import Path

from CrawlTasks.industry.crawler_config_jiemian_business import (
    CRAWLER_CONFIG as JIEMIAN_CONFIG,
)
from IntelligenceCrawler.ProjectCatalog import scan_crawler_catalog
from IntelligenceCrawler.URLFilter import validate_url_filter_samples


ROOT = Path(__file__).resolve().parent.parent


def test_jiemian_uses_lightweight_public_business_and_industry_entries():
    assert JIEMIAN_CONFIG["discoverer_name"] == "ListPageDiscoverer"
    assert JIEMIAN_CONFIG["entry_points"] == {
        "china_business": "https://www.jiemian.com/lists/2.html",
        "china_industry": "https://www.jiemian.com/lists/28.html",
    }
    assert JIEMIAN_CONFIG["d_fetcher_name"] == "RequestsFetcher"
    assert JIEMIAN_CONFIG["e_fetcher_name"] == "RequestsFetcher"
    assert JIEMIAN_CONFIG["extractor_name"] == "Trafilatura"


def test_china_industry_source_filters_and_handlers_are_safe():
    assert validate_url_filter_samples(JIEMIAN_CONFIG) == []
    assert JIEMIAN_CONFIG["article_filter"] is None
    assert JIEMIAN_CONFIG["content_handler"] is None
    assert JIEMIAN_CONFIG["exception_handler"] is None


def test_jiemian_business_uses_bounded_article_cards():
    params = JIEMIAN_CONFIG["discoverer_init_param"]
    assert params["scope_selector"] == ".card-list"
    assert params["extraction_mode"] == "card"
    assert params["same_host_only"] is True


def test_china_industry_tasks_are_catalogued_under_industry_domain():
    entries = scan_crawler_catalog(
        ROOT / "CrawlTasks",
        ROOT / "_config" / "collector_example.json",
    )
    by_name = {entry.name: entry for entry in entries}

    assert by_name["jiemian_business"].group == "industry"
    assert by_name["jiemian_business"].discoverer == "ListPageDiscoverer"
    assert by_name["jiemian_business"].regression_sample_count == 6
