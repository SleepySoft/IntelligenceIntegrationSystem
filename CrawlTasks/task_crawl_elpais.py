from functools import partial

import Scraper.PlaywrightRawScraper
from GlobalConfig import APPLIED_NATIONAL_TIMEOUT_MS
from MyPythonUtility.easy_config import EasyConfig
from ServiceEngine import ServiceContext
from Tools.RSSFetcher import fetch_feed
from Scraper.PlaywrightRenderedScraper import fetch_content
from Scrubber.HTMLConvertor import html_content_converter
from Scrubber.UnicodeSanitizer import sanitize_unicode_string
from Workflow.CommonFeedsCrawFlow import feeds_craw_flow

# https://elpais.com/
# https://elpais.com/info/rss/

feed_list = {
    "最新消息": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/ultimas-noticias/portada",
    "观看次数最多": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/lo-mas-visto/portada",
    "社会": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/sociedad/portada",
    "国际": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/internacional/portada",
    "西班牙": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/espana/portada",
    "经济": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/economia/portada",
    "科学": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/ciencia/portada",
    "商业": "https://feeds.elpais.com/mrss-s/list/ep/site/elpais.com/section/economia/subsection/negocios",
}


config: EasyConfig | None = None


def module_init(service_context: ServiceContext):
    global config
    config = service_context.config


def start_task(stop_event):
    feeds_craw_flow('elpais',
                    feed_list,
                    stop_event,
                    config,
                    15 * 60,

                    partial(fetch_feed, scraper=Scraper.PlaywrightRawScraper, proxy=config.get('collector.global_site_proxy', {})),
                    partial(fetch_content, timeout_ms=APPLIED_NATIONAL_TIMEOUT_MS, proxy=config.get('collector.global_site_proxy', {}), format='lxml'),
                    [
                        partial(html_content_converter, selectors=['article[id="main-content"]']),
                        partial(sanitize_unicode_string, max_length=10240 * 5)
                    ])

