# CrawlerConfig.py - Guardian（RSS，全静态，双 RequestsFetcher）
# 依据 doc/feed_20261017_survey.md：列表页与文章页均验证可抓；官方规则 {栏目}/rss
CRAWLER_CONFIG = {
    # === Components init parameters ===

    'd_fetcher_name': 'RequestsFetcher',
    'd_fetcher_init_param': {'log_callback': print, 'proxy': None, 'timeout_s': 20},

    'e_fetcher_name': 'RequestsFetcher',
    'e_fetcher_init_param': {'log_callback': print, 'proxy': None, 'timeout_s': 20},

    'discoverer_name': 'RSSDiscoverer',
    'discoverer_init_param': {'verbose': False},

    'extractor_name': 'Trafilatura',
    'extractor_init_param': {'verbose': False},

    # ======== Crawl parameters ========

    'entry_points': {
        'world': 'https://www.theguardian.com/world/rss',
        'politics': 'https://www.theguardian.com/politics/rss',
        'business': 'https://www.theguardian.com/uk/business/rss',
    },
    'period_filter': (None, None),
    'channel_filter': None,
    'd_fetcher_kwargs': {'wait_until': 'networkidle', 'wait_for_selector': None, 'wait_for_timeout_s': 20, 'scroll_pages': 0},
    'e_fetcher_kwargs': {'wait_until': 'networkidle', 'wait_for_selector': None, 'wait_for_timeout_s': 20, 'scroll_pages': 0},
    'extractor_kwargs': {},

    # === Handler functions (injected by run_common_flow) ===

    'article_filter': None,
    'content_handler': None,
    'exception_handler': None,
}


if __name__ == "__main__":
    import traceback
    try:
        from IntelligenceCrawler.CrawlPipeline import run_pipeline, save_article_to_disk
        CRAWLER_CONFIG['content_handler'] = save_article_to_disk
        run_pipeline('default', CRAWLER_CONFIG)
    except Exception as e:
        print(str(e))
        print(traceback.format_exc())
