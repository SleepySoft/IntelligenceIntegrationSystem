from CrawlTasks.news.crawler_config_i24news import (
    ARTICLE_SELECTOR,
    CRAWLER_CONFIG as I24_CONFIG,
    _is_relevant_article as i24_relevant,
)
from CrawlTasks.news.crawler_config_jpost import (
    CRAWLER_CONFIG as JPOST_CONFIG,
    _is_relevant_article as jpost_relevant,
)


def test_jpost_uses_news_rss_feeds_and_rejects_non_news_sections():
    assert JPOST_CONFIG["discoverer_name"] == "RSSDiscoverer"
    assert set(JPOST_CONFIG["entry_points"]) == {
        "israel", "politics_diplomacy", "middle_east",
    }
    assert all(
        value.startswith("https://www.jpost.com/rss/")
        for value in JPOST_CONFIG["entry_points"].values()
    )
    assert jpost_relevant(
        "https://www.jpost.com/israel-news/defense-news/article-912648")
    assert jpost_relevant(
        "https://www.jpost.com/middle-east/iran-news/article-123456")
    assert not jpost_relevant(
        "https://www.jpost.com/opinion/article-123456")
    assert not jpost_relevant(
        "https://www.jpost.com/food-recipes/article-123456")


def test_i24_uses_exact_article_scope_and_region_allowlist():
    params = I24_CONFIG["discoverer_init_param"]
    assert params["scope_selector"] == ARTICLE_SELECTOR
    assert params["extraction_mode"] == "card"
    assert params["same_host_only"] is True
    assert i24_relevant(
        "https://www.i24news.tv/en/news/israel/politics/artc-example")
    assert i24_relevant(
        "https://www.i24news.tv/en/news/middle-east/the-gulf/artc-example")
    assert not i24_relevant(
        "https://www.i24news.tv/en/news/international/europe/artc-example")
    assert not i24_relevant(
        "https://www.i24news.tv/en/news/israel/analysis-opinion/artc-example")
