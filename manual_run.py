import os
import logging
import threading
import traceback
from functools import partial

from CrawlerServiceEngine import ServiceContext
from IntelligenceCrawler.CrawlerGovernanceBackend import CrawlerGovernanceBackend
from MyPythonUtility.easy_config import EasyConfig
from GlobalConfig import APPLIED_NATIONAL_TIMEOUT_MS
from Scrubber.HTMLConvertor import html_content_converter
from Scrubber.UnicodeSanitizer import sanitize_unicode_string
from Workflow.RssFeedsBasedCrawlFlow import fetch_process_article
from IntelligenceCrawler.CrawlerGovernanceCore import GovernanceManager


logger = logging.getLogger(__name__)


def drive_module(module):
    crawler_governance = GovernanceManager(
        db_path='manual_run_governance.db',
        files_path='manual_run_governance_files'
    )

    governance_backend = CrawlerGovernanceBackend(crawler_governance)
    governance_backend.start_service(blocking=False)

    service_context = ServiceContext(
        module_logger=logger,
        module_config=None,
        crawler_governor=crawler_governance
    )

    # set_intelligence_sink(None)
    stop_event = threading.Event()

    module.module_init(service_context)
    while not stop_event.is_set():
        module.start_task(stop_event)


def fetch_by_request_scraper(url: str):
    """
    Includes: cbc
    :param url:
    :return:
    """
    from Scraper.RequestsScraper import fetch_content

    config = EasyConfig()

    text = fetch_process_article(
        url,
        partial(fetch_content, timeout_ms=APPLIED_NATIONAL_TIMEOUT_MS, proxy=config.get('collector.global_site_proxy', {}), format='lxml'),
        [
            partial(html_content_converter, selector='div[data-cy="storyWrapper"]'),
            partial(sanitize_unicode_string, max_length=10240 * 5)
        ])
    print(text)


def main():
    # from CrawlTasks import task_crawl_chinanews
    # drive_module(task_crawl_chinanews)

    # from CrawlTasks import task_crawl_people
    # drive_module(task_crawl_people)

    # from CrawlTasks import task_crawl_voanews
    # drive_module(task_crawl_voanews)

    # from CrawlTasks import task_crawl_cbc
    # drive_module(task_crawl_cbc)

    # from CrawlTasks import task_crawl_investing
    # drive_module(task_crawl_investing)

    # from CrawlTasks import task_crawl_bbc
    # drive_module(task_crawl_bbc)

    # from CrawlTasks import task_crawl_rfi
    # drive_module(task_crawl_rfi)

    # from CrawlTasks import task_crawl_dw
    # drive_module(task_crawl_dw)

    # from CrawlTasks import task_crawl_abc
    # drive_module(task_crawl_abc)

    # from CrawlTasks import task_crawl_aljazeera
    # drive_module(task_crawl_aljazeera)

    # from CrawlTasks import task_crawl_sputniknews_cn
    # drive_module(task_crawl_sputniknews_cn)

    # from CrawlTasks import task_crawl_nhk
    # drive_module(task_crawl_nhk)

    # from CrawlTasks import task_crawl_aa
    # drive_module(task_crawl_aa)

    # from CrawlTasks import task_crawl_nhk_ic
    # drive_module(task_crawl_nhk_ic)

    # from CrawlTasks import task_crawl_elpais
    # drive_module(task_crawl_elpais)

    # from CrawlTasks import task_crawl_tass
    # drive_module(task_crawl_tass)

    from CrawlTasks import task_crawl_news_cn
    drive_module(task_crawl_news_cn)

    # fetch_by_request_scraper('https://www.cbc.ca/news/science/india-flood-cloudburst-glacier-1.7603074?cmp=rss')

    pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(e)
        traceback.print_exc()
