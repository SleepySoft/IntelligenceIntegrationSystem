"""Agência Brasil 英文栏目抓取配置。

只读取更新量较低的英文页面，并限定到政治、经济、国际、司法和环境；不接体育、
生活、教育及葡萄牙语全量 Feed，防止巴西单一来源淹没南美区域信号。
"""


ARTICLE_SELECTOR = ", ".join(
    f'a[href^="/en/{section}/"][href*="/noticia/"]'
    for section in ("politica", "economia", "internacional", "justica", "meio-ambiente")
)


def _is_relevant_article(url: str) -> bool:
    path = str(url or "").lower()
    return (
        "agenciabrasil.ebc.com.br/en/" in path
        and "/noticia/" in path
        and any(f"/{section}/" in path for section in (
            "politica", "economia", "internacional", "justica", "meio-ambiente",
        ))
    )


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
        "english_latest": "https://agenciabrasil.ebc.com.br/en/ultimas",
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
    "url_filter": {
        "allowed_hosts": ["agenciabrasil.ebc.com.br"],
        "path_includes": [
            "/en/politica/noticia/", "/en/economia/noticia/",
            "/en/internacional/noticia/", "/en/justica/noticia/",
            "/en/meio-ambiente/noticia/",
        ],
        "query_policy": "allow",
    },
    "url_filter_samples": [
        {
            "url": "https://agenciabrasil.ebc.com.br/en/politica/noticia/2026-10/example",
            "allowed": True,
        },
        {
            "url": "https://agenciabrasil.ebc.com.br/en/esportes/noticia/2026-10/example",
            "allowed": False,
        },
        {
            "url": "https://outside.test/en/politica/noticia/2026-10/example",
            "allowed": False,
        },
    ],
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
