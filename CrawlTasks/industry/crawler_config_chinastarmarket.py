"""科创板日报：科技公司、半导体、AI 与硬科技公司行动。"""


ARTICLE_SELECTOR = 'a[href^="/detail/"]'


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
        "scope_selector": ARTICLE_SELECTOR,
        "manual_specified_signature": None,
        "extraction_mode": "card",
        "same_host_only": True,
    },
    "extractor_name": "Trafilatura",
    "extractor_init_param": {"verbose": False},
    "entry_points": {
        "technology_companies": "https://www.chinastarmarket.cn/",
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
        "allowed_hosts": ["www.chinastarmarket.cn"],
        "include_subdomains": False,
        "path_includes": ["/detail/"],
        "path_excludes": ["/telegraph/", "/subject/", "/activity/"],
        "query_policy": "reject",
    },
    "url_filter_samples": [
        {
            "url": "https://www.chinastarmarket.cn/detail/2517432",
            "allowed": True,
        },
        {
            "url": "https://www.chinastarmarket.cn/detail/2517089",
            "allowed": True,
        },
        {
            "url": "https://www.chinastarmarket.cn/telegraph",
            "allowed": False,
        },
        {
            "url": "https://www.chinastarmarket.cn/subject/1321",
            "allowed": False,
        },
        {
            "url": "https://www.chinastarmarket.cn/detail/2517432?from=home",
            "allowed": False,
        },
        {
            "url": "https://outside.test/detail/2517432",
            "allowed": False,
        },
    ],
    "article_filter": None,
    "content_handler": None,
    "exception_handler": None,
}
