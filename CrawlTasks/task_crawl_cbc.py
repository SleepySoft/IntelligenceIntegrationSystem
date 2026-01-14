from functools import partial
from Tools.RSSFetcher import fetch_feed
from CrawlerServiceEngine import ServiceContext
from MyPythonUtility.easy_config import EasyConfig
from GlobalConfig import APPLIED_NATIONAL_TIMEOUT_MS
from Scrubber.HTMLConvertor import html_content_converter
from Scrubber.UnicodeSanitizer import sanitize_unicode_string
from Workflow.CommonFlowUtility import CrawlContext
from Workflow.CommonFeedsCrawFlow import build_crawl_ctx_by_service_ctx, feeds_craw_flow

from Scraper.RequestsScraper import fetch_content


# https://www.cbc.ca/rss/

feed_list = {
    "World News": "https://www.cbc.ca/webfeed/rss/rss-world",
    "Canada News": "https://www.cbc.ca/webfeed/rss/rss-canada",
    "Business News": "https://www.cbc.ca/webfeed/rss/rss-business",
    "Technology News": "https://www.cbc.ca/webfeed/rss/rss-technology",
}


config: EasyConfig | None = None
crawl_context: CrawlContext | None = None


def module_init(service_context: ServiceContext):
    global config
    global crawl_context
    config = service_context.config
    crawl_context = build_crawl_ctx_by_service_ctx('cbc', service_context)


def start_task(stop_event):
    feeds_craw_flow('cbc',
                    feed_list,
                    stop_event,
                    config,
                    15 * 60,

                    partial(fetch_feed, fetch_content=fetch_content, proxy=config.get('collector.global_site_proxy', {})),
                    partial(fetch_content, timeout_ms=APPLIED_NATIONAL_TIMEOUT_MS, proxy=config.get('collector.global_site_proxy', {}), format='lxml'),
                    [
                        partial(html_content_converter, selectors='div[data-cy="storyWrapper"]'),
                        partial(sanitize_unicode_string, max_length=10240 * 5)
                    ],
                    crawl_context)

