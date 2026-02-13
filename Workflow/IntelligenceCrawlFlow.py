# --------------------------------------------------------- #
#  IntelligenceCrawlFlow.py                                 #
#   - Using common functions in CommonFlowUtility.py        #
#   - Implement CrawlPipeline's handlers                    #
# --------------------------------------------------------- #

import datetime
import threading
from uuid import uuid4
from functools import partial

from CrawlerServiceEngine import ServiceContext
from IntelligenceCrawler.CrawlPipeline import run_pipeline
from MyPythonUtility.easy_config import EasyConfig
from Workflow.CommonFeedsCrawFlow import build_crawl_ctx_by_service_ctx
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


class CommonIntelligenceCrawlFlow:
    def __init__(self, name: str, service_context: ServiceContext):
        self.name = name
        self.proj_config = service_context.config
        self.crawl_context = build_crawl_ctx_by_service_ctx(name, service_context)

    def run_common_flow(self, local_crawler_config: dict, stop_event: threading.Event):
        # Override generated config by user config file.
        http_proxy = self.proj_config.get('collector.global_site_proxy.http', '')
        local_crawler_config['d_fetcher_init_param']['proxy'] = http_proxy
        local_crawler_config['e_fetcher_init_param']['proxy'] = http_proxy

        local_crawler_config['article_filter'] = partial(intelligence_crawler_filter, context=self.crawl_context)
        local_crawler_config['content_handler'] = partial(intelligence_crawler_result_handler, context=self.crawl_context)

        run_pipeline(self.name, local_crawler_config, crawler_governor=self.crawl_context.crawler_governor)

        # Check and submit cached data.
        self.crawl_context.submit_cached_data(10)

        # TODO: Deprecated.
        self.crawl_context.crawler_governor.wait_interval(60 * 15, stop_event=stop_event)

