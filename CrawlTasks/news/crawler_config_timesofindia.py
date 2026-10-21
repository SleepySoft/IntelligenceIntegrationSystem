# CrawlerConfig.py - Times of India（列表页静态；南亚视角）
# 依据 doc/feed_20261017_survey.md：列表页 200/226-397 链接，正文 16-19k
CRAWLER_CONFIG = {
    # === Components init parameters ===

    'd_fetcher_name': 'RequestsFetcher',
    'd_fetcher_init_param': {'log_callback': print, 'proxy': None, 'timeout_s': 10},

    'e_fetcher_name': 'RequestsFetcher',
    'e_fetcher_init_param': {'log_callback': print, 'proxy': None, 'timeout_s': 20},

    'discoverer_name': 'ListPageDiscoverer',
    'discoverer_init_param': {
        'verbose': False,
        'manual_specified_signature': None,
        'scope_selector': None,
        # world 列表混有推广站、专题站和 App 跳转链接。
        'same_host_only': True,
    },

    'extractor_name': 'Trafilatura',
    'extractor_init_param': {'verbose': False},

    # ======== Crawl parameters ========

    'entry_points': {
        'world': 'https://timesofindia.indiatimes.com/world',
        # india/business 栏目页链接混杂（混入版块页与截断正文），启发式发现质量不达标，暂不接。
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
