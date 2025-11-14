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

    # submit_ihub_url = config.get('collector.submit_ihub_url', f'http://127.0.0.1:{DEFAULT_IHUB_PORT}')
    # collector_tokens = config.get('intelligence_hub_web_service.collector.tokens')
    # token = collector_tokens[0] if collector_tokens else DEFAULT_COLLECTOR_TOKEN
    #
    # context.logger.info(f'submit to URL: {submit_ihub_url}, token = {token}.')
    #
    # crawl_record = CrawlRecord(['crawl_record', flow_name])
    # crawl_statistics = CrawlStatistics()

    # ------------------------------------------------------------------------------------------------------------------

    for feed_name, feed_url in { **feeds, "cached": "" }.items():
        if stop_event.is_set():
            break
        level_names = [flow_name, feed_name]

        # feed_statistics = {
        #     'total': 0,
        #     'index': 0,
        #     'success': 0,
        #     'skip': 0,
        # }

        # ----------------------------------- Fetch and Parse feeds -----------------------------------

        context.logger.info(f'Processing feed: [{feed_name}] - {feed_url}')

        try:
            result = fetch_feed(feed_url)
            if result.fatal:
                raise ProcessError(error_text = '\n'.join(result.errors))
            # feed_statistics['total'] = len(result.entries)
            context.crawl_statistics.counter_log(level_names, 'success')
        except Exception as e:
            context.logger.error(f"Process feed fail: {feed_url} - {str(e)}")
            context.crawl_record.increment_error_count(feed_url)
            context.crawl_statistics.counter_log(level_names, 'exception')
            continue

        context.logger.info(f'Feed: [{feed_name}] process finished.')

        # ----------------------------------- Process Articles in Feed ----------------------------------

        for article in result.entries + ['']:
            try:
                # feed_statistics['index'] += 1

                article_link = article.link
                if article_link:
                    context.logger.debug(f'Processing article: {article_link}')

                # ----------------------------------- Check Duplication ----------------------------------

                # url_status = crawl_record.get_url_status(article_link, from_db=False)
                # if url_status >= STATUS_SUCCESS:
                #     raise ProcessSkip('already exists', article_link)
                # elif url_status <= STATUS_UNKNOWN:
                #     pass        # <- Process going on here
                # elif url_status == STATUS_ERROR:
                #     url_error_count = crawl_record.get_error_count(article_link, from_db=False)
                #     if url_error_count < 0:
                #         raise ProcessProblem('db_error', article_link)
                #     if url_error_count >= CRAWL_ERROR_THRESHOLD:
                #         raise ProcessSkip('max retry exceed', article_link)
                #     else:
                #         pass    # <- Process going on here
                # else:   # STATUS_DB_ERROR
                #     raise ProcessProblem('db_error', article_link)
                #
                # # Also keep this check to make it compatible
                # if has_url(article_link):
                #     raise ProcessSkip('already exists', article_link)

                # ------------------------------- Fetch and Parse articles ------------------------------

                # TODO: Use a loop to process cached data.
                collected_data = context.check_get_cached_data(article_link)
                if collected_data:
                    context.logger.debug(f'[cache] Get data from cache: {article_link}')

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
                        token='',

                        title=article.title,
                        authors=article.authors,
                        content=text,
                        pub_time=article.published,
                        informant=article.link
                    )

                context.submit_collected_data(collected_data, level_names)

            except Exception as e:
                context.handle_process_exception(e)

                # if _intelligence_sink:
                #     result = _intelligence_sink(submit_ihub_url, collected_data, 10)
                #     if result.get('status', 'success') == 'error':
                #         if not cached_data:
                #             cache_content(article_link, collected_data)
                #             context.logger.info(f'[cache] Cache item: {article_link}')
                #         raise ProcessProblem('commit_error')
                #     else:
                #         if cached_data:
                #             drop_cached_content(article_link)
                #             context.logger.info(f'[cache] Submitted and remove item: {article_link}')

                # if text:
                #     success, file_path = to_file_and_history(
                #         article_link, text, article.title, feed_name, '.md')
                #     # TODO: Actually, with CrawlRecord, we don't need this.
                #     if not success:
                #         context.logger.info(f'Save content {file_path} fail.')
                #
                # feed_statistics['success'] += 1
                # print('.', end='', flush=True)
                # context.logger.debug(f'Article finished.')
                # crawl_record.record_url_status(article_link, STATUS_SUCCESS)
                # crawl_statistics.sub_item_log(level_names, article_link, 'success')

            # except ProcessSkip:
            #     feed_statistics['skip'] += 1
            #     print('*', end='', flush=True)
            #     context.logger.debug(f'Article skipped.')
            #
            # except ProcessIgnore as e:
            #     feed_statistics['skip'] += 1
            #     print('o', end='', flush=True)
            #     context.logger.debug(f'Article ignored.')
            #     crawl_record.record_url_status(e.item, STATUS_IGNORED)
            #     crawl_statistics.sub_item_log(level_names, e.item, e.reason)
            #
            # except ProcessProblem as e:
            #     if e.problem == 'db_error':
            #         # DB error, not content error, just ignore and retry at next loop.
            #         context.logger.error('Crawl record DB Error.')
            #     elif e.problem in ['fetch_error', 'persists_error', 'commit_error']:
            #         # If just commit error, just retry with cache.
            #         # Persists error, actually once we're starting use CrawRecord. We don't need this anymore
            #         print('x', end='', flush=True)
            #         context.logger.error(e.problem)
            #         if e.problem != 'commit_error':
            #             crawl_record.increment_error_count(e.item)
            #         crawl_statistics.sub_item_log(level_names, e.item, e.problem)
            #     else:
            #         pass

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

    # ----------------------------------------- Log all feeds counter -----------------------------------------

    # crawl_statistics.dump_counters(['flow_name'])
    context.logger.info(f"Finished one loop and rest for {update_interval_s} seconds ...")

    # ------------------------------------------ Delay and Wait for Next Loop ------------------------------------------

    # Wait for next loop and check event per 5s.
    remaining = update_interval_s
    while remaining > 0 and not stop_event.is_set():
        sleep_time = min(5, remaining)
        time.sleep(sleep_time)
        remaining -= sleep_time
