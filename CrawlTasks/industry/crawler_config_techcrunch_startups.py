"""TechCrunch Startups：科技公司融资、并购、管理层与战略变化。"""


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
    "entry_points": {
        "technology_company_actions": "https://techcrunch.com/category/startups/feed/",
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
        "wait_for_timeout_s": 30,
        "scroll_pages": 0,
    },
    "extractor_kwargs": {},
    "url_filter": {
        "allowed_hosts": ["techcrunch.com"],
        "include_subdomains": False,
        "path_includes": [],
        "path_excludes": [
            "/events/",
            "/podcast/",
            "/video/",
            "founder-summit",
            "save-up-to",
        ],
        "query_policy": "reject",
    },
    "url_filter_samples": [
        {
            "url": "https://techcrunch.com/2026/10/27/guidewire-buys-hyperexponential-for-1-7b-in-cash/",
            "allowed": True,
        },
        {
            "url": "https://techcrunch.com/2026/10/27/meta-ai-director-rohit-patel-leaves-to-take-on-microsoft-office-and-google-workspace/",
            "allowed": True,
        },
        {
            "url": "https://techcrunch.com/2026/10/23/techcrunch-founder-summit-2026-roundtables/",
            "allowed": False,
        },
        {
            "url": "https://techcrunch.com/podcast/equity/",
            "allowed": False,
        },
        {
            "url": "https://techcrunch.com/2026/10/27/example/?utm_source=rss",
            "allowed": False,
        },
        {
            "url": "https://outside.test/2026/10/27/technology-company-action/",
            "allowed": False,
        },
    ],
    "article_filter": None,
    "content_handler": None,
    "exception_handler": None,
}
