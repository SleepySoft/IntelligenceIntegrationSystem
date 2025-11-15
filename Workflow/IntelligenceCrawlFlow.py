import datetime
from uuid import uuid4
from typing import List

from Workflow.CommonFlowUtility import CrawlContext
from IntelligenceCrawler.Extractor import ExtractionResult
from ServiceComponent.IntelligenceHubDefines import CollectedData


def intelligence_crawler_result_handler(url: str, result: ExtractionResult, context: CrawlContext, levels: List[str]):
    try:
        collected_data = CollectedData(
            UUID=str(uuid4()),
            token='-',                  # Will be filled in submit_collected_data()

            title=result.metadata.get('title', ''),
            authors=result.metadata.get('authors', []),
            content=result.markdown_content,
            pub_time=result.metadata.get('date', datetime.datetime.now()),
            informant=url
        )
        context.submit_collected_data(collected_data, levels)
    except Exception as e:
        context.handle_process_exception(e)
    finally:
        pass


def intelligence_crawler_fileter(url: str, context: CrawlContext, levels: List[str]) -> bool:
    if collected_data := context.check_get_cached_data(url):
        context.logger.info(f'[cache] Get data from cache: {url}')
        try:
            context.submit_collected_data(collected_data, levels)
        except Exception as e:
            context.handle_process_exception(e)
        return False
    return True


def intelligence_crawler_exception_handler(url: str, e: Exception, context: CrawlContext, levels: List[str]):
    context.handle_process_exception(e)

