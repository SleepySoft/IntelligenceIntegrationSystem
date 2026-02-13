from functools import partial
from Tools.RSSFetcher import fetch_feed
from CrawlerServiceEngine import ServiceContext
from MyPythonUtility.easy_config import EasyConfig
from GlobalConfig import APPLIED_NATIONAL_TIMEOUT_MS
from Scrubber.HTMLConvertor import html_content_converter
from Scrubber.UnicodeSanitizer import sanitize_unicode_string
from Workflow.CommonFlowUtility import CrawlContext
from Workflow.RssFeedsBasedCrawlFlow import build_crawl_ctx_by_service_ctx, feeds_craw_flow

from Scraper.RequestsScraper import fetch_content as feed_fetcher
from Scraper.PlaywrightRenderedScraper import fetch_content as article_fetcher


# https://www.investing.com/


feed_list = {
    "Analysis": "https://www.investing.com/rss/121899.rss",
    "Market Overview": "https://www.investing.com/rss/market_overview.rss",

    "SWOT Analysis News": "https://www.investing.com/rss/news_1060.rss",
    "Stock Analyst Ratings": "https://www.investing.com/rss/news_1061.rss",
    "Cryptocurrency News": "https://www.investing.com/rss/news_301.rss",
    "Company News": "https://www.investing.com/rss/news_356.rss",
    "Insider Trading News": "https://www.investing.com/rss/news_357.rss",
    "Forex News": "https://www.investing.com/rss/news_1.rss",
    "Commodities & Futures News": "https://www.investing.com/rss/news_11.rss",
    "Stock Market News": "https://www.investing.com/rss/news_25.rss",
    "Economic Indicators News": "https://www.investing.com/rss/news_95.rss",
    "Economy News": "https://www.investing.com/rss/news_14.rss",
}


config: EasyConfig | None = None
crawl_context: CrawlContext | None = None


def module_init(service_context: ServiceContext):
    global config
    global crawl_context
    config = service_context.config
    crawl_context = build_crawl_ctx_by_service_ctx('investing', service_context)


def start_task(stop_event):
    feeds_craw_flow('investing',
                    feed_list,
                    stop_event,
                    config,
                    15 * 60,

                    partial(fetch_feed, fetch_content=feed_fetcher, proxy=config.get('collector.global_site_proxy', {})),
                    partial(article_fetcher, timeout_ms=APPLIED_NATIONAL_TIMEOUT_MS, proxy=config.get('collector.global_site_proxy', {}), format='lxml'),
                    [
                        partial(html_content_converter, selectors='div[id="article"]'),
                        partial(sanitize_unicode_string, max_length=10240 * 5)
                    ],
                    crawl_context)
