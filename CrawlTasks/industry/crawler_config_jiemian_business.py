"""界面新闻商业与工业：聚焦中国公司与产业行动的公开列表页。"""


CRAWLER_CONFIG = {
    "d_fetcher_name": "RequestsFetcher",
    "d_fetcher_init_param": {
        "log_callback": print,
        "proxy": None,
        "timeout_s": 20,
    },
    "e_fetcher_name": "RequestsFetcher",
    "e_fetcher_init_param": {
        "log_callback": print,
        "proxy": None,
        "timeout_s": 25,
    },
    "discoverer_name": "ListPageDiscoverer",
    "discoverer_init_param": {
        "verbose": False,
        "scope_selector": ".card-list",
        "manual_specified_signature": None,
        "extraction_mode": "card",
        "same_host_only": True,
    },
    "extractor_name": "Trafilatura",
    "extractor_init_param": {"verbose": False},
    "entry_points": {
        "china_business": "https://www.jiemian.com/lists/2.html",
        "china_industry": "https://www.jiemian.com/lists/28.html",
    },
    "period_filter": (None, None),
    "channel_filter": None,
    "d_fetcher_kwargs": {
        "wait_until": "domcontentloaded",
        "wait_for_selector": None,
        "wait_for_timeout_s": 20,
        "scroll_pages": 0,
    },
    "e_fetcher_kwargs": {
        "wait_until": "domcontentloaded",
        "wait_for_selector": None,
        "wait_for_timeout_s": 25,
        "scroll_pages": 0,
    },
    "extractor_kwargs": {},
    "url_filter": {
        "allowed_hosts": ["www.jiemian.com"],
        "include_subdomains": False,
        "path_includes": ["/article/"],
        "path_excludes": ["/video/", "/special/"],
        "query_policy": "reject",
    },
    "url_filter_samples": [
        {
            "url": "https://www.jiemian.com/article/15248751.html",
            "allowed": True,
        },
        {
            "url": "https://www.jiemian.com/article/15241851.html",
            "allowed": True,
        },
        {
            "url": "https://www.jiemian.com/lists/2.html",
            "allowed": False,
        },
        {
            "url": "https://www.jiemian.com/article/15248751.html?from=channel",
            "allowed": False,
        },
        {
            "url": "https://m.jiemian.com/article/15248751.html",
            "allowed": False,
        },
        {
            "url": "https://outside.test/article/15248751.html",
            "allowed": False,
        },
    ],
    "article_filter": None,
    "content_handler": None,
    "exception_handler": None,
}
