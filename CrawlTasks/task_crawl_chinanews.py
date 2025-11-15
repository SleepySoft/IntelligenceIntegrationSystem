from functools import partial
from ServiceEngine import ServiceContext
from Tools.RSSFetcher import fetch_feed
from MyPythonUtility.easy_config import EasyConfig
from GlobalConfig import APPLIED_INTERNAL_TIMEOUT_MS
from Scrubber.HTMLConvertor import html_content_converter
from Scrubber.UnicodeSanitizer import sanitize_unicode_string
from Workflow.CommonFlowUtility import CrawlContext
from Workflow.CommonFeedsCrawFlow import build_crawl_ctx_by_config, feeds_craw_flow

from Scraper.RequestsScraper import fetch_content as feed_fetcher
from Scraper.PlaywrightRenderedScraper import fetch_content as article_fetcher


feed_list = {
    "即时新闻": "https://www.chinanews.com.cn/rss/scroll-news.xml",
    "要闻导读": "https://www.chinanews.com.cn/rss/importnews.xml",
    "时政新闻": "https://www.chinanews.com.cn/rss/china.xml",
    "国际新闻": "https://www.chinanews.com.cn/rss/world.xml",
    "财经新闻": "https://www.chinanews.com.cn/rss/finance.xml"
}


config: EasyConfig | None = None
crawl_context: CrawlContext | None = None


def module_init(service_context: ServiceContext):
    global config
    global crawl_context
    config = service_context.config
    crawl_context = build_crawl_ctx_by_config('chinanews', config)


def start_task(stop_event):
    feeds_craw_flow('chinanews',
                    feed_list,
                    stop_event,
                    config,
                    15 * 60,

                    partial(fetch_feed, fetch_content=feed_fetcher, proxy=config.get('collector.global_site_proxy', {})),
                    partial(article_fetcher, timeout_ms=APPLIED_INTERNAL_TIMEOUT_MS, proxy=config.get('collector.global_site_proxy', {})),
                    [
                        partial(html_content_converter, selectors='div.left_zw'),
                        partial(sanitize_unicode_string, max_length=10240)
                    ],
                    crawl_context)


# def start_task(stop_event):
#     for feed_name, feed_url in feed_list.items():
#         if stop_event.is_set():
#             break
#         try:
#             print(f'Process feed: {feed_name} : {feed_url}')
#             result = fetch_feed(feed_url, Scraper.RequestsScraper, {})
#
#             for article in result['entries']:
#                 article_link = article['link']
#
#                 if ContentHistory.has_url(article_link):
#                     continue
#
#                 print(f'|__Fetch article: {article_link}')
#                 content = fetch_content(article_link, 20 * 1000)
#
#                 raw_html = content['content']
#                 if not raw_html:
#                     logging.error('  |__Got empty HTML content.')
#                     continue
#
#                 # TODO: If an article always convert fail. Need a special treatment.
#
#                 markdown = html_content_converter(raw_html, 'div.left_zw')
#                 if not markdown:
#                     logging.error('  |__Got empty content when converting to markdown.')
#                     continue
#
#                 clean_text = sanitize_unicode_string(markdown, max_length = 10000)
#                 if not clean_text:
#                     logging.error('  |__Got empty content when sanitizing unicode string.')
#                     continue
#
#                 success, file_path = to_file_and_history(article_link, clean_text, article['title'], feed_name, '.md')
#                 if not success:
#                     logging.error(f'  |__Save content {file_path} fail.')
#                     continue
#
#         except Exception as e:
#             print(f"Process feed fail: {feed_url} - {str(e)}")
#             print(traceback.format_exc())
#
#     # Wait 10 minutes for next loop and check event per 5s.
#     # noinspection PyTypeChecker
#     for _ in range(10 * 60 // 5):
#         if stop_event.is_set():
#             break
#         time.sleep(5)
