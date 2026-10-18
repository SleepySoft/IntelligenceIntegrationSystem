import calendar
import time
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
# VOA 主英语栏目自 2025-03 起冻结。2026-10 复查时，下列语言服务的英文译稿 RSS
# 仍持续更新；正文仍位于 voanews.com，且沿用相同页面结构。

feed_list = {
    "Dari English": "https://www.voanews.com/api/zqroorl-vomx-tpeitpoq",
    "Kurdish English": "https://www.voanews.com/api/z-bqoyl-vomx-tpertpov",
    "Korean English": "https://www.voanews.com/api/zprooil-vomx-tpe_tpop",
    "Mandarin English": "https://www.voanews.com/api/ziroool-vomx-tpemtpoi",
    "Pashto English": "https://www.voanews.com/api/zorooql-vomx-tpeptpoo",
    "Persian English": "https://www.voanews.com/api/z_roo_l-vomx-tpevtpo_",
    "Russian English": "https://www.voanews.com/api/zmroopl-vomx-tpeytpom",
    "Ukrainian English": "https://www.voanews.com/api/zrrootl-vomx-tpeotpor",
}

MAX_FEED_AGE_DAYS = 14


config: EasyConfig | None = None
crawl_context: CrawlContext | None = None


def module_init(service_context: ServiceContext):
    global config
    global crawl_context
    config = service_context.config
    crawl_context = build_crawl_ctx_by_service_ctx('voanews', service_context)


def fetch_current_voa_feed(url, fetch_content, proxy=None, headless=True):
    """拒绝再次冻结的 RSS，避免旧内容被长期报告为抓取成功。"""
    result = fetch_feed(
        url, fetch_content=fetch_content, proxy=proxy, headless=headless)
    if result.fatal or not result.entries:
        return result

    published_timestamps = [
        calendar.timegm(article.published)
        for article in result.entries
        if article.published is not None
    ]
    if published_timestamps and max(published_timestamps) < (
            time.time() - MAX_FEED_AGE_DAYS * 24 * 60 * 60):
        result.fatal = True
        result.errors.append(
            f'VOA feed is stale for more than {MAX_FEED_AGE_DAYS} days: {url}')
    return result


def start_task(stop_event):
    feeds_craw_flow('voanews',
                    feed_list,
                    stop_event,
                    config,
                    15 * 60,

                    partial(fetch_current_voa_feed, fetch_content=feed_fetcher,
                            proxy=config.get('collector.global_site_proxy', {})),
                    partial(article_fetcher, timeout_ms=APPLIED_NATIONAL_TIMEOUT_MS, proxy=config.get('collector.global_site_proxy', {})),
                    [
                        partial(html_content_converter, selectors=['.title.pg-title', 'div.published', 'div.wsw, div.m-t-md']),
                        partial(sanitize_unicode_string, max_length=10240)
                    ],
                    crawl_context)

