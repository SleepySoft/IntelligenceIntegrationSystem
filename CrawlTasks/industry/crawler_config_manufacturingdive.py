"""Manufacturing Dive：制造业公司行动 RSS 源。"""


CRAWLER_CONFIG = {
    "d_fetcher_name": "RequestsFetcher",
    "d_fetcher_init_param": {
        "log_callback": print,
        "proxy": None,
        "timeout_s": 20,
    },
    # 正文端对普通 requests 返回 403；使用浏览器获取公开文章页。
    "e_fetcher_name": "PlaywrightFetcher",
    "e_fetcher_init_param": {
        "log_callback": print,
        "proxy": None,
        "timeout_s": 30,
        "stealth": True,
        "pause_browser": False,
        "render_page": True,
    },
    "discoverer_name": "RSSDiscoverer",
    "discoverer_init_param": {"verbose": False},
    "extractor_name": "Trafilatura",
    "extractor_init_param": {"verbose": False},
    "entry_points": {
        "manufacturing_news": "https://www.manufacturingdive.com/feeds/news/",
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
        "wait_for_selector": "article",
        "wait_for_timeout_s": 30,
        "scroll_pages": 0,
    },
    "extractor_kwargs": {},
    "url_filter": {
        "allowed_hosts": ["www.manufacturingdive.com"],
        "include_subdomains": False,
        "path_includes": ["/news/"],
        "path_excludes": ["/spons/", "/press-release/"],
        "query_policy": "reject",
    },
    "url_filter_samples": [
        {
            "url": (
                "https://www.manufacturingdive.com/news/"
                "audi-investing-4b-to-build-its-first-us-plant-in-georgia/872769/"
            ),
            "allowed": True,
        },
        {
            "url": (
                "https://www.manufacturingdive.com/news/"
                "fcc-grants-intel-1-year-waiver-permit-use-foreign-produced-robots/872745/"
            ),
            "allowed": True,
        },
        {
            "url": (
                "https://www.manufacturingdive.com/spons/"
                "the-next-manufacturing-ai-challenge-isnt-adoption-its-scale/833752/"
            ),
            "allowed": False,
        },
        {
            "url": "https://www.manufacturingdive.com/press-release/example/",
            "allowed": False,
        },
        {
            "url": "https://www.manufacturingdive.com/news/example/123/?utm_source=rss",
            "allowed": False,
        },
        {
            "url": "https://outside.test/news/manufacturing-example/",
            "allowed": False,
        },
    ],
    "article_filter": None,
    "content_handler": None,
    "exception_handler": None,
}
