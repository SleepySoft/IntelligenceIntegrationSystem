from pathlib import Path

from CrawlTasks.industry.crawler_config_chinastarmarket import (
    CRAWLER_CONFIG as CHINASTARMARKET_CONFIG,
)
from CrawlTasks.industry.crawler_config_ijiwei import (
    CRAWLER_CONFIG as IJIWEI_CONFIG,
)
from IntelligenceCrawler.CrawlerCodeGenerator import CrawlerCodeGenerator
from IntelligenceCrawler.ProjectCatalog import scan_crawler_catalog
from IntelligenceCrawler.URLFilter import validate_url_filter_samples


ROOT = Path(__file__).resolve().parent.parent


def test_technology_sources_use_narrow_static_lists_and_requests():
    assert CHINASTARMARKET_CONFIG["entry_points"] == {
        "technology_companies": "https://www.chinastarmarket.cn/",
    }
    assert IJIWEI_CONFIG["entry_points"] == {
        "semiconductor_and_ict": "https://www.laoyaoba.com/",
    }
    for config in (CHINASTARMARKET_CONFIG, IJIWEI_CONFIG):
        assert config["discoverer_name"] == "ListPageDiscoverer"
        assert config["discoverer_init_param"]["extraction_mode"] == "card"
        assert config["discoverer_init_param"]["same_host_only"] is True
        assert config["d_fetcher_name"] == "RequestsFetcher"
        assert config["e_fetcher_name"] == "RequestsFetcher"
        assert config["extractor_name"] == "Trafilatura"


def test_technology_source_filters_and_handlers_are_safe():
    for config in (CHINASTARMARKET_CONFIG, IJIWEI_CONFIG):
        assert validate_url_filter_samples(config) == []
        assert len(config["url_filter_samples"]) >= 6
        assert config["url_filter"]["query_policy"] == "reject"
        assert config["article_filter"] is None
        assert config["content_handler"] is None
        assert config["exception_handler"] is None


def test_technology_source_configs_round_trip_through_generator():
    generator = CrawlerCodeGenerator()
    for config in (CHINASTARMARKET_CONFIG, IJIWEI_CONFIG):
        namespace = {}
        exec(generator.generate_code_from_config(config), namespace)
        assert namespace["CRAWLER_CONFIG"] == config


def test_technology_tasks_are_catalogued_under_industry_domain():
    entries = scan_crawler_catalog(
        ROOT / "CrawlTasks",
        ROOT / "_config" / "collector_example.json",
    )
    by_name = {entry.name: entry for entry in entries}

    for name in ("chinastarmarket", "ijiwei"):
        assert by_name[name].group == "industry"
        assert by_name[name].discoverer == "ListPageDiscoverer"
        assert by_name[name].regression_sample_count == 6
        assert by_name[name].task_path is not None
        assert not by_name[name].disabled
