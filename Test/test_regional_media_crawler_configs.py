from CrawlTasks.news.crawler_config_agenciabrasil import (
    CRAWLER_CONFIG as AGENCIA_CONFIG,
    _is_relevant_article as agencia_relevant,
)
from CrawlTasks.news.crawler_config_cna import (
    CRAWLER_CONFIG as CNA_CONFIG,
    _is_relevant_article as cna_relevant,
)
from CrawlTasks.news.crawler_config_jakartapost import (
    CRAWLER_CONFIG as JAKARTA_CONFIG,
    _is_relevant_article as jakarta_relevant,
)
from CrawlTasks.news.crawler_config_mercopress import (
    CRAWLER_CONFIG as MERCOPRESS_CONFIG,
    _is_relevant_article as mercopress_relevant,
)
from IntelligenceCrawler.URLFilter import validate_url_filter_samples


def test_cna_only_uses_asia_feed_and_rejects_non_news_content():
    assert set(CNA_CONFIG["entry_points"]) == {"asia"}
    assert cna_relevant("https://www.channelnewsasia.com/asia/example-123")
    assert not cna_relevant("https://www.channelnewsasia.com/sport/example-123")
    assert not cna_relevant("https://www.channelnewsasia.com/asia/commentary/example-123")


def test_jakarta_post_is_limited_to_canonical_indonesia_and_business_urls():
    assert set(JAKARTA_CONFIG["entry_points"]) == {"indonesia"}
    assert jakarta_relevant(
        "https://www.thejakartapost.com/indonesia/2026/10/20/example")
    assert jakarta_relevant(
        "https://www.thejakartapost.com/business/2026/10/20/example")
    assert not jakarta_relevant(
        "https://www.thejakartapost.com/indonesia/2026/10/20/example?utm_source=x")
    assert not jakarta_relevant(
        "https://www.thejakartapost.com/world/2026/10/20/example")


def test_mercopress_only_uses_latin_america_feed():
    assert set(MERCOPRESS_CONFIG["entry_points"]) == {"latin_america"}
    assert mercopress_relevant(
        "https://en.mercopress.com/2026/10/20/example?utm_source=feed")
    assert not mercopress_relevant(
        "https://en.mercopress.com/tourism/2026/10/20/example")


def test_agencia_brasil_only_accepts_selected_english_sections():
    assert set(AGENCIA_CONFIG["entry_points"]) == {"english_latest"}
    assert agencia_relevant(
        "https://agenciabrasil.ebc.com.br/en/politica/noticia/2026-10/example")
    assert agencia_relevant(
        "https://agenciabrasil.ebc.com.br/en/meio-ambiente/noticia/2026-10/example")
    assert not agencia_relevant(
        "https://agenciabrasil.ebc.com.br/en/esportes/noticia/2026-10/example")
    assert not agencia_relevant(
        "https://agenciabrasil.ebc.com.br/politica/noticia/2026-10/example")


def test_regional_configs_declarative_filter_samples_match_custom_filters():
    for config in (CNA_CONFIG, JAKARTA_CONFIG, MERCOPRESS_CONFIG, AGENCIA_CONFIG):
        assert config["url_filter"]
        assert len(config["url_filter_samples"]) >= 3
        assert validate_url_filter_samples(config) == []
