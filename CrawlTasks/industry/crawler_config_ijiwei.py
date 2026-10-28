"""爱集微：半导体、ICT、AI 硬件与科技公司行动。"""


# 首页还有视频、活动、专题、咨询报告和推广区域。只取随时间更新的新闻区与焦点区，
# 再由 industry 分析协议判断是否存在具体公司经营行动。
ARTICLE_SELECTOR = (
    'section.news a[href^="/n/"], '
    'section.focus a[href^="/n/"]'
)


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
        "semiconductor_and_ict": "https://www.laoyaoba.com/",
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
        "allowed_hosts": ["www.laoyaoba.com"],
        "include_subdomains": False,
        "path_includes": ["/n/"],
        "path_excludes": [
            "/api/", "/activity/", "/jwvideos/", "/special/",
        ],
        "query_policy": "reject",
    },
    "url_filter_samples": [
        {
            "url": "https://www.laoyaoba.com/n/1125314",
            "allowed": True,
        },
        {
            "url": "https://www.laoyaoba.com/n/1125715",
            "allowed": True,
        },
        {
            "url": "https://www.laoyaoba.com/activity/iicie/2026",
            "allowed": False,
        },
        {
            "url": "https://www.laoyaoba.com/jwvideos",
            "allowed": False,
        },
        {
            "url": "https://www.laoyaoba.com/n/1125314?from=home",
            "allowed": False,
        },
        {
            "url": "https://outside.test/n/1125314",
            "allowed": False,
        },
    ],
    "article_filter": None,
    "content_handler": None,
    "exception_handler": None,
}
