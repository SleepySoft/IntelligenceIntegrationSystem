"""i24NEWS 英文媒体抓取配置。

i24NEWS 没有供本任务使用的稳定 RSS。列表页正文已包含文章链接，因此使用精确 CSS
范围配合 Card 模式发现，随后以 URL 白名单二次限制到以色列和中东新闻。
"""


ARTICLE_SELECTOR = 'a[href*="/en/news/"][href*="/artc-"]'


def _is_relevant_article(url: str) -> bool:
    path = str(url or "").lower()
    return any(prefix in path for prefix in (
        "/en/news/israel/",
        "/en/news/israel-election-s-2026/",
        "/en/news/middle-east/",
    )) and "/analysis-opinion/" not in path


CRAWLER_CONFIG = {
    "d_fetcher_name": "RequestsFetcher",
    "d_fetcher_init_param": {"log_callback": print, "proxy": None, "timeout_s": 20},
    "e_fetcher_name": "RequestsFetcher",
    "e_fetcher_init_param": {"log_callback": print, "proxy": None, "timeout_s": 25},
    "discoverer_name": "ListPageDiscoverer",
    "discoverer_init_param": {
        "verbose": False,
        "scope_selector": ARTICLE_SELECTOR,
        "manual_specified_signature": None,
        "extraction_mode": "card",
        "same_host_only": True,
    },
    "extractor_name": "Trafilatura",
    "extractor_init_param": {"verbose": False},
    "entry_points": {
        "israel": "https://www.i24news.tv/en/news/israel",
        "israel_elections": "https://www.i24news.tv/en/news/israel-election-s-2026",
        "middle_east": "https://www.i24news.tv/en/news/middle-east",
    },
    "period_filter": (None, None),
    "channel_filter": None,
    "d_fetcher_kwargs": {
        "wait_until": "networkidle", "wait_for_selector": None,
        "wait_for_timeout_s": 20, "scroll_pages": 0,
    },
    "e_fetcher_kwargs": {
        "wait_until": "networkidle", "wait_for_selector": None,
        "wait_for_timeout_s": 25, "scroll_pages": 0,
    },
    "extractor_kwargs": {},
    "article_filter": _is_relevant_article,
    "content_handler": None,
    "exception_handler": None,
}


if __name__ == "__main__":
    import traceback

    try:
        from IntelligenceCrawler.CrawlPipeline import run_pipeline, save_article_to_disk

        CRAWLER_CONFIG["content_handler"] = save_article_to_disk
        run_pipeline("default", CRAWLER_CONFIG)
    except Exception as exc:
        print(str(exc))
        print(traceback.format_exc())
