from functools import partial

import Scraper.RequestsScraper
from GlobalConfig import APPLIED_PROXY, APPLIED_INTERNAL_TIMEOUT_MS
from MyPythonUtility.easy_config import EasyConfig
from ServiceEngine import ServiceContext
from Tools.RSSFetcher import fetch_feed
from Scraper.PlaywrightRenderedScraper import fetch_content
from Scrubber.HTMLConvertor import html_content_converter
from Scrubber.UnicodeSanitizer import sanitize_unicode_string
from Workflow.CommonFeedsCrawFlow import feeds_craw_flow

feed_list = {
    "时政新闻": "http://www.people.com.cn/rss/politics.xml",
    "国际新闻": "http://www.people.com.cn/rss/world.xml",
    "台港澳新闻": "http://www.people.com.cn/rss/haixia.xml",
    "军事新闻": "http://www.people.com.cn/rss/military.xml",
    "全部新闻": "http://www.people.com.cn/rss/ywkx.xml"
}


config: EasyConfig | None = None


def module_init(service_context: ServiceContext):
    global config
    config = service_context.config


def start_task(stop_event):
    feeds_craw_flow('people',
                    feed_list,
                    stop_event,
                    config,
                    15 * 60,

                    partial(fetch_feed, scraper=Scraper.RequestsScraper, proxy=config.get('collector.global_site_proxy', {})),
                    partial(fetch_content, timeout_ms=APPLIED_INTERNAL_TIMEOUT_MS, proxy=config.get('collector.global_site_proxy', {})),
                    [
                        partial(html_content_converter, selectors='div.rm_txt, div.text_con_left'),
                        partial(sanitize_unicode_string, max_length=10240)
                    ])
