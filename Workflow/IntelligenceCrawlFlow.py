# --------------------------------------------------------- #
#  IntelligenceCrawlFlow.py                                 #
#   - Using common functions in CommonFlowUtility.py        #
#   - Implement CrawlPipeline's handlers                    #
# --------------------------------------------------------- #

import datetime
from uuid import uuid4

from Workflow.CommonFlowUtility import CrawlContext
from IntelligenceCrawler.Extractor import ExtractionResult
from ServiceComponent.IntelligenceHubDefines_v2 import CollectedData


def intelligence_crawler_filter(
        url: str,
        context: CrawlContext
) -> bool:
    # Don't make it so complex. Just check and submit cached data after crawl loop.
    return not context.is_url_in_cache(url)

    # if collected_data := context.check_get_cached_data(url):
    #     context.logger.info(f'[cache] Get data from cache: {url}')
    #     with context.crawler_governor.transaction(url, channel_group) as task:
    #         try:
    #             context.submit_collected_data(channel_group, collected_data, task)
    #             task.success('Cached content committed.')
    #         except Exception as e:
    #             context.handle_process_exception(task, e)
    #         return False
    # return True


def intelligence_crawler_result_handler(
        url: str,
        group: str,
        result: ExtractionResult,
        context: CrawlContext
):
    # Handle exception outside.
    collected_data = CollectedData(
        UUID=str(uuid4()),
        token='-',                  # Will be filled in submit_collected_data()

        title=result.metadata.get('title', ''),
        authors=result.metadata.get('authors', []),
        content=result.markdown_content,
        pub_time=result.metadata.get('date', datetime.datetime.now()),
        informant=url
    )
    context.submit_collected_data(group, collected_data)


def intelligence_crawler_exception_handler(
        url: str,
        e: Exception,
        context: CrawlContext
):
    pass

