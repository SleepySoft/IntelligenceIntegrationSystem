from functools import partial
from Tools.RSSFetcher import fetch_feed
from CrawlerServiceEngine import ServiceContext
from MyPythonUtility.easy_config import EasyConfig
from GlobalConfig import APPLIED_NATIONAL_TIMEOUT_MS
from Scrubber.HTMLConvertor import html_content_converter
from Scrubber.UnicodeSanitizer import sanitize_unicode_string
from Workflow.CommonFlowUtility import CrawlContext
from Workflow.CommonFeedsCrawFlow import build_crawl_ctx_by_service_ctx, feeds_craw_flow

from Scraper.RequestsScraper import fetch_content as feed_fetcher
from Scraper.PlaywrightRawScraper import fetch_content as article_fetcher


# https://www.abc.net.au/

feed_list = {
    "Top Stories": "https://www.abc.net.au/news/feed/10719986/rss.xml",
    "World": "https://www.abc.net.au/news/feed/104217382/rss.xml",
    "Business": "https://www.abc.net.au/news/feed/104217374/rss.xml",
    "Politics": "https://www.abc.net.au/news/feed/104217372/rss.xml",
}


config: EasyConfig | None = None
crawl_context: CrawlContext | None = None


def module_init(service_context: ServiceContext):
    global config
    global crawl_context
    config = service_context.config
    crawl_context = build_crawl_ctx_by_service_ctx('abc', service_context)


def start_task(stop_event):
    feeds_craw_flow('abc',
                    feed_list,
                    stop_event,
                    config,
                    15 * 60,

                    partial(fetch_feed, fetch_content=feed_fetcher, proxy=config.get('collector.global_site_proxy', {})),
                    partial(article_fetcher, timeout_ms=APPLIED_NATIONAL_TIMEOUT_MS, proxy=config.get('collector.global_site_proxy', {}), format='lxml'),
                    [
                        partial(html_content_converter, selectors=['div[class*="ArticleHeadlineTitle_container"]', 'div[class*="ArticleWeb_article"]']),
                        partial(sanitize_unicode_string, max_length=10240 * 5)
                    ],
                    crawl_context)

