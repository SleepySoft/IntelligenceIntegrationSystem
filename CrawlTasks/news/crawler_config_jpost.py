"""Jerusalem Post 英文媒体抓取配置。

使用站方公开 RSS 发现文章；只选择以色列本地、政治外交和中东栏目，避免把文化、
体育、生活方式等低价值频道灌入分析主链。
"""


def _is_relevant_article(url: str) -> bool:
    path = str(url or "").lower()
    return any(prefix in path for prefix in (
        "/israel-news/",
        "/middle-east/",
        "/arab-israeli-conflict/",
        "/iran-news/",
    )) and "/opinion/" not in path


CRAWLER_CONFIG = {
    "d_fetcher_name": "RequestsFetcher",
    "d_fetcher_init_param": {"log_callback": print, "proxy": None, "timeout_s": 20},
    "e_fetcher_name": "RequestsFetcher",
    "e_fetcher_init_param": {"log_callback": print, "proxy": None, "timeout_s": 25},
    "discoverer_name": "RSSDiscoverer",
    "discoverer_init_param": {"verbose": False},
    "extractor_name": "Trafilatura",
    "extractor_init_param": {"verbose": False},
    "entry_points": {
        "israel": "https://www.jpost.com/rss/rssfeedsisraelnews.aspx",
        "politics_diplomacy": "https://www.jpost.com/rss/rssfeedspoliticsdiplomacy.aspx",
        "middle_east": "https://www.jpost.com/rss/rssfeedsmiddleeastnews.aspx",
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
