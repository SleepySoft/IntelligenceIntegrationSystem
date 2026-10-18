# CrawlerConfig.py - Africanews（列表页静态；非洲视角，现有源空白）
# 依据 doc/feed_20261017_survey.md：列表页 200/48-64 链接，正文 6k
CRAWLER_CONFIG = {
    # === Components init parameters ===

    'd_fetcher_name': 'RequestsFetcher',
    'd_fetcher_init_param': {'log_callback': print, 'proxy': None, 'timeout_s': 10},

    'e_fetcher_name': 'RequestsFetcher',
    'e_fetcher_init_param': {'log_callback': print, 'proxy': None, 'timeout_s': 20},

    'discoverer_name': 'ListPageDiscoverer',
    'discoverer_init_param': {'verbose': False, 'manual_specified_signature': None, 'scope_selector': None},

    'extractor_name': 'Trafilatura',
    'extractor_init_param': {'verbose': False},

    # ======== Crawl parameters ========

    'entry_points': {
        'news': 'https://www.africanews.com/news/',
        'business': 'https://www.africanews.com/business/',
    },
    'period_filter': (None, None),
    'channel_filter': None,
    'd_fetcher_kwargs': {'wait_until': 'networkidle', 'wait_for_selector': None, 'wait_for_timeout_s': 10, 'scroll_pages': 0},
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
