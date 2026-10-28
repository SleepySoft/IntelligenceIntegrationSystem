"""IndustryWeek：制造企业扩张、经营、组织与技术部署。"""


INDUSTRYWEEK_FEED = (
    "https://www.industryweek.com/__rss/website-scheduled-content.xml?"
    "input=%7B%22sectionAlias%22%3A%22home%22%7D"
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
        "timeout_s": 30,
    },
    "discoverer_name": "RSSDiscoverer",
    "discoverer_init_param": {"verbose": False},
    "extractor_name": "Trafilatura",
    "extractor_init_param": {"verbose": False},
    "entry_points": {"manufacturing_companies": INDUSTRYWEEK_FEED},
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
        "wait_for_timeout_s": 30,
        "scroll_pages": 0,
    },
    "extractor_kwargs": {},
    "url_filter": {
        "allowed_hosts": ["www.industryweek.com"],
        "include_subdomains": False,
        "path_includes": [
            "/leadership/companies-executives/news/",
            "/leadership/corporate-finance/",
            "/operations/plant-operations/",
            "/technology-and-iiot/",
        ],
        "path_excludes": [
            "/podcast/", "/media-gallery/", "/video/", "/supply-chain/",
        ],
        "query_policy": "reject",
    },
    "url_filter_samples": [
        {
            "url": (
                "https://www.industryweek.com/leadership/companies-executives/news/"
                "55414225/5-manufacturers-breaking-ground-on-new-us-facilities"
            ),
            "allowed": True,
        },
        {
            "url": (
                "https://www.industryweek.com/leadership/companies-executives/news/"
                "55413917/musk-and-tesla-target-a-big-2030-sales-target"
            ),
            "allowed": True,
        },
        {
            "url": (
                "https://www.industryweek.com/operations/continuous-improvement/podcast/"
                "55414250/plan-do-check-act-pdca-focus-on-the-learning"
            ),
            "allowed": False,
        },
        {
            "url": "https://www.industryweek.com/supply-chain/news/55388479/example",
            "allowed": False,
        },
        {
            "url": (
                "https://www.industryweek.com/leadership/companies-executives/news/"
                "55413917/example?utm_source=rss"
            ),
            "allowed": False,
        },
        {
            "url": "https://outside.test/leadership/companies-executives/news/example",
            "allowed": False,
        },
    ],
    "article_filter": None,
    "content_handler": None,
    "exception_handler": None,
}
