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

# https://www.voanews.com/rssfeeds
# Too much video content.

feed_list = {
    "USA": "https://www.voanews.com/api/zqboml-vomx-tpeivmy",
    "All About America": "https://www.voanews.com/api/zb__qtl-vomx-tpeqrtqq",
    "Immigration": "https://www.voanews.com/api/zgvmqyl-vomx-tpe-qvqv",

    "Africa": "https://www.voanews.com/api/z-botl-vomx-tpertmq",
    "East Asia": "https://www.voanews.com/api/zobo_l-vomx-tpepvmv",
    "China News": "https://www.voanews.com/api/zmjuqtl-vomx-tpey_jqq",

    "South & Central Asia": "https://www.voanews.com/api/z_-mqyl-vomx-tpevyvqv",
    "Middle East": "https://www.voanews.com/api/zrbopl-vomx-tpeovm_",
    "Iran": "https://www.voanews.com/api/zvgmqil-vomx-tpeumvqm",

    "Europe": "https://www.voanews.com/api/zjbovl-vomx-tpebvmr",
    "Ukraine": "https://www.voanews.com/api/zt_rqyl-vomx-tpekboq_",
    "Americas": "https://www.voanews.com/api/zoripl-vomx-tpeptmm",

    "Technology": "https://www.voanews.com/api/zyritl-vomx-tpettmq",
    "Economy": "https://www.voanews.com/api/zyboql-vomx-tpetvmi",
}


config: EasyConfig | None = None
crawl_context: CrawlContext | None = None


def module_init(service_context: ServiceContext):
    global config
    global crawl_context
    config = service_context.config
    crawl_context = build_crawl_ctx_by_service_ctx('voanews', service_context)


def start_task(stop_event):
    feeds_craw_flow('voanews',
                    feed_list,
                    stop_event,
                    config,
                    15 * 60,

                    partial(fetch_feed, fetch_content=feed_fetcher, proxy=config.get('collector.global_site_proxy', {})),
                    partial(article_fetcher, timeout_ms=APPLIED_NATIONAL_TIMEOUT_MS, proxy=config.get('collector.global_site_proxy', {})),
                    [
                        partial(html_content_converter, selectors=['.title.pg-title', 'div.published', 'div.wsw, div.m-t-md']),
                        partial(sanitize_unicode_string, max_length=10240)
                    ],
                    crawl_context)

