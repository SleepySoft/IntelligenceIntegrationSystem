import urllib3
import threading
from uuid import uuid4
from typing import Callable, TypedDict, Dict, List

from GlobalConfig import DEFAULT_COLLECTOR_TOKEN
from IntelligenceHub import CollectedData
from CrawlerServiceEngine import ServiceContext
from MyPythonUtility.easy_config import EasyConfig
from IntelligenceHubWebService import DEFAULT_IHUB_PORT
from Workflow.CommonFlowUtility import CrawlContext
from Tools.RSSFetcher import FeedData, RssItem
from Tools.ProcessCotrolException import ProcessError, ProcessProblem, ProcessSkip

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class FetchContentResult(TypedDict):
    content: str


# --------------------------------- Helper Functions ---------------------------------

def build_crawl_ctx_by_service_ctx(name, service_context: ServiceContext) -> CrawlContext:
    config = service_context.config
    governor = service_context.crawler_governor
    submit_ihub_url = config.get('collector.submit_ihub_url', f'http://127.0.0.1:{DEFAULT_IHUB_PORT}')
    collector_tokens = config.get('intelligence_hub_web_service.collector.tokens')
    token = collector_tokens[0] if collector_tokens else DEFAULT_COLLECTOR_TOKEN
    crawl_context = CrawlContext(name, submit_ihub_url, token, governor)
    return crawl_context


def fetch_process_article(article: RssItem,
                          fetch_content: Callable[[str], FetchContentResult],
                          scrubbers: List[Callable[[str], str]]
                          ) -> CollectedData:
    try:
        content = fetch_content(article.link)
    except Exception:
        raise ProcessProblem('fetch_error', article.link)

    # ---------------------------------------------------
    # TODO: If an article always convert fail. Need a special treatment.

    raw_html = content['content']
    if not raw_html:
        raise ProcessSkip('Got empty content at fetch.')

    text = raw_html
    for scrubber in scrubbers:
        text = scrubber(text)
        if not text:
            break
    if not text:
        raise ProcessSkip('Got empty content after scrub.')

    # --------------- Pack Fetched Data ---------------

    collected_data = CollectedData(
        UUID=str(uuid4()),
        token='-',  # Will be filled in submit_collected_data()

        title=article.title,
        authors=article.authors,
        content=text,
        pub_time=article.published,
        informant=article.link
    )

    return collected_data


def get_and_submit_article(group_path: List[str],
                           article: RssItem,
                           context: CrawlContext,
                           fetch_content: Callable[[str], FetchContentResult],
                           scrubbers: List[Callable[[str], str]]
                           ):
    with context.crawler_governor.transaction(article.link, group_path) as task:
        try:
            if collected_data := context.check_get_cached_data(article.link):
                context.logger.info(f'[cache] Got data from cache: {article.link}')
            else:
                collected_data = fetch_process_article(article, fetch_content, scrubbers)

            # Temporary record this name for commit cache data.
            # Because if you don't record this name, the feed name of this article may lose.
            collected_data.temp_data['group_path'] = group_path

            context.submit_collected_data(collected_data)

            task.save_file(collected_data.content, collected_data.title)
            task.success()

        except Exception as e:
            context.handle_process_exception(task, e)


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

        if stop_event.is_set(): break
        if feed_name == 'cached': continue      # Placeholder for processing cached data.

        group_path = [flow_name, feed_name]
        context.crawler_governor.register_group_metadata(group_path, feed_url)

        if not context.crawler_governor.should_crawl(feed_url):
            continue

        # ----------------------------------- Fetch and Parse feeds -----------------------------------

        context.logger.info(f'Processing feed: [{feed_name}] - {feed_url}')

        with context.crawler_governor.transaction(feed_url, group_path) as task:
            try:
                result = fetch_feed(feed_url)
                if result.fatal:
                    raise ProcessError(error_text = '\n'.join(result.errors))
                task.success()
            except Exception as e:
                context.logger.error(f"Process feed fail: {feed_url} - {str(e)}")
                task.fail_temp(state_msg=str(e))
                continue

        context.crawler_governor.start_round(group_path, len(result.entries))
        context.logger.info(f'Feed: [{feed_name}] process finished, found {len(result.entries)} articles.')

        # ----------------------------------- Process Articles in Feed ----------------------------------

        for article in result.entries:
            if not article.link:
                context.logger.info(f'Got empty article link from feed: {feed_url}')
                continue

            if not context.crawler_governor.should_crawl(article.link):
                continue

            context.logger.info(f'Processing article: {article.link}')
            get_and_submit_article(group_path, article, context, fetch_content, scrubbers)

            context.crawler_governor.wait_interval(1)

        # TODO: Remove next_run_delay
        context.crawler_governor.finish_round(group_path, 60 * 15)

    # ----------------------------------------- Process Cached Data ----------------------------------------

    context.submit_cached_data()

    # ------------------------------------ Delay and Wait for Next Loop ------------------------------------

    context.crawler_governor.wait_interval(60 * 15, stop_event)
