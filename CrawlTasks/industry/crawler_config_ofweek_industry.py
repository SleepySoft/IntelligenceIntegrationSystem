"""OFweek：半导体、电子工程与机器人公司的产业行动。"""


# 三个频道首页还挂有大量排行、资料库和历史推荐链接。只取当前焦点轮播与正文卡片，
# 避免首次接入把整页历史存量一次性灌入分析队列。
ARTICLE_SELECTOR = (
    'a.swiper-slide[href*="/ART-"], '
    '.deep_read_cont_title a[href*="/ART-"]'
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
        "china_semiconductor": "https://semi.ofweek.com/",
        "china_electronics": "https://ee.ofweek.com/",
        "china_robotics": "https://robot.ofweek.com/",
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
        "allowed_hosts": [
            "semi.ofweek.com",
            "ee.ofweek.com",
            "robot.ofweek.com",
        ],
        "include_subdomains": False,
        "path_includes": ["/art-"],
        "path_excludes": ["/topic/", "/special/", "/activity/"],
        "query_policy": "reject",
    },
    "url_filter_samples": [
        {
            "url": "https://semi.ofweek.com/2026-10/ART-202530-8420-30708473.html",
            "allowed": True,
        },
        {
            "url": "https://ee.ofweek.com/2026-10/ART-8110-2801-30709240.html",
            "allowed": True,
        },
        {
            "url": "https://robot.ofweek.com/2026-10/ART-898890-12003-30709229.html",
            "allowed": True,
        },
        {
            "url": "https://semi.ofweek.com/CAT-202558-industry.html",
            "allowed": False,
        },
        {
            "url": "https://semi.ofweek.com/2026-10/ART-202530-8420-30708473.html?from=home",
            "allowed": False,
        },
        {
            "url": "https://outside.test/2026-10/ART-202530-8420-30708473.html",
            "allowed": False,
        },
    ],
    "article_filter": None,
    "content_handler": None,
    "exception_handler": None,
}
