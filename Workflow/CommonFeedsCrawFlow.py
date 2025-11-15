import time
import logging
import urllib3
import threading
from uuid import uuid4
from typing import Callable, TypedDict, Dict, List, Tuple

from GlobalConfig import DEFAULT_COLLECTOR_TOKEN
from IntelligenceHub import CollectedData
from MyPythonUtility.easy_config import EasyConfig
from PyLoggingBackend.LogUtility import get_tls_logger
from Tools.ContentHistory import has_url
from IntelligenceHubWebService import post_collected_intelligence, DEFAULT_IHUB_PORT
from Streamer.ToFileAndHistory import to_file_and_history
from Tools.CrawlRecord import CrawlRecord, STATUS_ERROR, STATUS_SUCCESS, STATUS_DB_ERROR, STATUS_UNKNOWN, STATUS_IGNORED
from Tools.CrawlStatistics import CrawlStatistics
from Tools.ProcessCotrolException import ProcessSkip, ProcessError, ProcessTerminate, ProcessProblem, ProcessIgnore
from Tools.RSSFetcher import FeedData
from Workflow.CommonFlowUtility import CrawlContext

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


CRAWL_ERROR_FEED_FETCH = 'Feed fetch error'
CRAWL_ERROR_FEED_PARSE = 'Feed parse error'
CRAWL_ERROR_ARTICLE_FETCH = 'Article fetch error'


class FetchContentResult(TypedDict):
    content: str


def build_crawl_ctx_by_config(name, config: EasyConfig) -> CrawlContext:
    submit_ihub_url = config.get('collector.submit_ihub_url', f'http://127.0.0.1:{DEFAULT_IHUB_PORT}')
    collector_tokens = config.get('intelligence_hub_web_service.collector.tokens')
    token = collector_tokens[0] if collector_tokens else DEFAULT_COLLECTOR_TOKEN
    crawl_context = CrawlContext(name, submit_ihub_url, token)
    return crawl_context


# --------------------------------- Helper Functions ---------------------------------

def fetch_process_article(article_link: str,
                          fetch_content: Callable[[str], FetchContentResult],
                          scrubbers: List[Callable[[str], str]]) -> Tuple[str, str]:
    try:
        content = fetch_content(article_link)
    except Exception:
        return '', 'fetch'

    raw_html = content['content']
    if not raw_html:
        # context.logger.error(f'{prefix}   |--Got empty HTML content.')
        # craw_statistics.sub_item_log(stat_name, article_link, 'fetch emtpy')
        return '', 'fetch'

    # TODO: If an article always convert fail. Need a special treatment.

    text = raw_html
    for scrubber in scrubbers:
        text = scrubber(text)
        if not text:
            break
    if not text:
        # context.logger.error(f'{prefix}   |--Got empty content when applying scrubber {str(scrubber)}.')
        # craw_statistics.sub_item_log(stat_name, article_link, 'scrub emtpy')
        return '', 'scrub'

    return text, ''


# ---------------------------------- Main process ----------------------------------

def feeds_craw_flow(flow_name: str,
                    feeds: Dict[str, str],
                    stop_event: threading.Event,
                    config: EasyConfig,
                    update_interval_s: int,

                    fetch_feed: Callable[[str], FeedData],
                    fetch_content: Callable[[str], FetchContentResult],
                    scrubbers: List[Callable[[str], str]],

                    context: CrawlContext
                    ):
    """
    A common feeds and their articles craw workflow. This workflow works in this sequence:
        fetch_feed -> for each feed: fetch_content -> apply scrubbers

    :param flow_name: The workflow name for logging and tracing.
    :param feeds: The feeds dict like:
                {
                    'feed name': 'feed link'
                }
    :param stop_event: The stop event to quit loop.
    :param config: The easy config. This function will get token from it.
    :param update_interval_s: The polling update interval in second.

    :param fetch_feed: The function to fetch feed. Function declaration:
                        fetch_feed(feed_url: str) -> dict
    :param fetch_content: The function to fetch web content by url. Function declaration:
                        fetch_content(article_link: str) -> dict
    :param scrubbers: The functions to process scrubbed text. Function declaration:
                        scrubber(text: str) -> str
    :param context: CrawlContext for data record and submission.

    :return: None
    """
    context.logger.info(f'starts work.')

    for feed_name, feed_url in { **feeds, "cached": "" }.items():
        if stop_event.is_set():
            break
        level_names = [flow_name, feed_name]

        # ----------------------------------- Fetch and Parse feeds -----------------------------------

        context.logger.info(f'Processing feed: [{feed_name}] - {feed_url}')

        try:
            result = fetch_feed(feed_url)
            if result.fatal:
                raise ProcessError(error_text = '\n'.join(result.errors))
            context.crawl_statistics.counter_log(level_names, 'success')
        except Exception as e:
            context.logger.error(f"Process feed fail: {feed_url} - {str(e)}")
            context.crawl_record.increment_error_count(feed_url)
            context.crawl_statistics.counter_log(level_names, 'exception')
            continue

        context.logger.info(f'Feed: [{feed_name}] process finished, found {len(result.entries)} articles.')

        # ----------------------------------- Process Articles in Feed ----------------------------------

        for article in result.entries:
            try:
                article_link = article.link
                if article_link:
                    context.logger.info(f'Processing article: {article_link}')

                if collected_data := context.check_get_cached_data(article_link):
                    context.logger.info(f'[cache] Get data from cache: {article_link}')

                else:
                    text, error_place = fetch_process_article(article_link, fetch_content, scrubbers)

                    # TODO: How to detect it's fetch issue or the empty content is work as design?
                    if error_place == 'fetch':
                        raise ProcessProblem('fetch_error', article_link)

                    if not text:
                        raise ProcessIgnore('empty when ' + error_place)

                    # --------------- Pack Fetched Data ---------------

                    collected_data = CollectedData(
                        UUID=str(uuid4()),
                        token='-',              # Will be filled in submit_collected_data()

                        title=article.title,
                        authors=article.authors,
                        content=text,
                        pub_time=article.published,
                        informant=article.link
                    )

                context.submit_collected_data(collected_data, level_names)

            except Exception as e:
                context.handle_process_exception(e)

        # ---------------------------------------- Log feed statistics ----------------------------------------

        # context.logger.info(f"Feed: {feed_name} finished.\n"
        #             f"     Total: {feed_statistics['total']}\n"
        #             f"     Success: {feed_statistics['success']}\n"
        #             f"     Skip: {feed_statistics['skip']}\n"
        #             f"     Fail: {feed_statistics['total'] - feed_statistics['success'] - feed_statistics['skip']}\n"
        #             f"     Total Cached Items: {len(_uncommit_content_cache)}")

        # print('-' * 80)
        # print(crawl_statistics.dump_sub_items(level_names, statuses=[
        #     'fetch emtpy', 'scrub emtpy', 'persists fail', 'exception']))
        # print()
        # print('=' * 100)
        # print()

    # ----------------------------------------- Process Cached Data -----------------------------------------

    context.submit_cached_data()

    # ---------------------------------------- Log all feeds counter ----------------------------------------

    # crawl_statistics.dump_counters(['flow_name'])
    context.logger.info(f"Finished one loop and rest for {update_interval_s} seconds ...")

    # ------------------------------------ Delay and Wait for Next Loop ------------------------------------

    # Wait for next loop and check event per 5s.
    remaining = update_interval_s
    while remaining > 0 and not stop_event.is_set():
        sleep_time = min(5, remaining)
        time.sleep(sleep_time)
        remaining -= sleep_time
