from pathlib import Path

from CrawlTasks.industry.crawler_config_industryweek import (
    CRAWLER_CONFIG as INDUSTRYWEEK_CONFIG,
)
from CrawlTasks.industry.crawler_config_ofweek_industry import (
    CRAWLER_CONFIG as OFWEEK_CONFIG,
)
from CrawlTasks.industry.crawler_config_techcrunch_startups import (
    CRAWLER_CONFIG as TECHCRUNCH_CONFIG,
)
from IntelligenceCrawler.CrawlerCodeGenerator import CrawlerCodeGenerator
from IntelligenceCrawler.ProjectCatalog import scan_crawler_catalog
from IntelligenceCrawler.URLFilter import validate_url_filter_samples


ROOT = Path(__file__).resolve().parent.parent
CONFIGS = (OFWEEK_CONFIG, INDUSTRYWEEK_CONFIG, TECHCRUNCH_CONFIG)


def test_new_industry_sources_use_narrow_public_entries():
    assert set(OFWEEK_CONFIG["entry_points"]) == {
        "china_semiconductor", "china_electronics", "china_robotics",
    }
    assert INDUSTRYWEEK_CONFIG["discoverer_name"] == "RSSDiscoverer"
    assert TECHCRUNCH_CONFIG["discoverer_name"] == "RSSDiscoverer"
    assert "/supply-chain/" in INDUSTRYWEEK_CONFIG["url_filter"]["path_excludes"]
    assert "founder-summit" in TECHCRUNCH_CONFIG["url_filter"]["path_excludes"]
    assert OFWEEK_CONFIG["discoverer_init_param"]["scope_selector"] == (
        'a.swiper-slide[href*="/ART-"], '
        '.deep_read_cont_title a[href*="/ART-"]'
    )


def test_new_industry_source_filters_and_handlers_are_safe():
    for config in CONFIGS:
        assert validate_url_filter_samples(config) == []
        assert config["article_filter"] is None
        assert config["content_handler"] is None
        assert config["exception_handler"] is None


def test_new_industry_configs_round_trip_through_generator():
    generator = CrawlerCodeGenerator()
    for config in CONFIGS:
        code = generator.generate_code_from_config(config)
        namespace = {}
        exec(code, namespace)
        generated = namespace["CRAWLER_CONFIG"]
        assert generated["entry_points"] == config["entry_points"]
        assert generated["url_filter"] == config["url_filter"]
        assert generated["url_filter_samples"] == config["url_filter_samples"]


def test_new_industry_tasks_are_catalogued_under_industry_domain():
    entries = scan_crawler_catalog(
        ROOT / "CrawlTasks",
        ROOT / "_config" / "collector_example.json",
    )
    by_name = {entry.name: entry for entry in entries}

    for name in ("ofweek_industry", "industryweek", "techcrunch_startups"):
        assert by_name[name].group == "industry"
        assert by_name[name].regression_sample_count == 6
