# --------------------------------------------------------- #
#  IntelligenceCrawlFlow.py                                 #
#   - Using common functions in CommonFlowUtility.py        #
#   - Implement CrawlPipeline's handlers                    #
# --------------------------------------------------------- #

import datetime
import threading
from uuid import uuid4
from typing import Optional
from functools import partial

from CrawlerServiceEngine import ServiceContext
from MyPythonUtility.easy_config import EasyConfig
from Workflow.CommonFlowUtility import CrawlContext
from IntelligenceCrawler.Extractor import ExtractionResult
from ServiceComponent.IntelligenceHubDefines_v2 import CollectedData
from Workflow.RssFeedsBasedCrawlFlow import build_crawl_ctx_by_service_ctx
from IntelligenceCrawler.CrawlPipeline import CrawlPipeline, build_pipeline, drive_pipeline_batch


DEFAULT_CRAWL_LOOP_DURATION = 15 * 60


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
        self.proj_config: EasyConfig = service_context.config
        self.crawl_context = build_crawl_ctx_by_service_ctx(name, service_context)

        self.pipeline: Optional[CrawlPipeline] = None

    def run_common_flow(self, local_crawler_config: dict, stop_event: threading.Event):
        # Override generated config by user config file.
        http_proxy = self.proj_config.get('collector.global_site_proxy.http', '')
        local_crawler_config['d_fetcher_init_param']['proxy'] = http_proxy
        local_crawler_config['e_fetcher_init_param']['proxy'] = http_proxy

        local_crawler_config['article_filter'] = partial(intelligence_crawler_filter, context=self.crawl_context)
        local_crawler_config['content_handler'] = partial(intelligence_crawler_result_handler, context=self.crawl_context)

        # Check and submit cached data.
        self.crawl_context.submit_cached_data(10)

        # Only create once.
        if not self.pipeline:
            self.pipeline = build_pipeline(name=self.name,
                                           config=local_crawler_config,
                                           log_callback=self.crawl_context.logger,
                                           crawler_governor=self.crawl_context.crawler_governor)

        # TODO: Split and shorted the scope of scheduler.
        with self.crawl_context.crawler_governor.schedule_pace(
                f"{self.name}", DEFAULT_CRAWL_LOOP_DURATION, None):
            drive_pipeline_batch(self.pipeline , local_crawler_config)
