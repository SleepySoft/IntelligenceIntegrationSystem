# ---------------------------------------------------------------------------------------- #
#　 CommonFlowUtility.py                                                                    #
#   - Common functions for both traditional crawl framework and IntelligenceCrawler         #
# ----------------------------------------------------------------------------------------- #

import logging
import urllib3
from logging import Logger

from GlobalConfig import DEFAULT_COLLECTOR_TOKEN
from IntelligenceCrawler.CrawlPipeline import format_exception_with_traceback
from IntelligenceHub import CollectedData
from PyLoggingBackend.LogUtility import get_tls_logger
from IntelligenceHubWebService import post_collected_intelligence
from Tools.ProcessCotrolException import ProcessSkip, ProcessProblem, ProcessIgnore
from IntelligenceCrawler.CrawlerGovernanceCore import GovernanceManager, CrawlSession

DEFAULT_CRAWL_ERROR_THRESHOLD = 3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ------------------------------------------------------------------------------------------------------------------

import threading
from typing import Dict, Any, Optional, Tuple
import random
from collections import defaultdict


class CrawlCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._uncommit_content_cache: Dict[str, Any] = {}

    def cache_len(self) -> int:
        with self._lock:
            return len(self._uncommit_content_cache)

    def is_in_cache(self, url: str):
        with self._lock:
            return url in self._uncommit_content_cache

    def cache_content(self, url: str, content: any):
        with self._lock:
            self._uncommit_content_cache[url] = content

    def pop_content(self, url: str) -> Optional[Any]:
        with self._lock:
            return self._uncommit_content_cache.pop(url, None)

    def pop_random_item(self) -> Optional[Tuple[str, Any]]:
        with self._lock:
            if self._uncommit_content_cache:
                return self._uncommit_content_cache.popitem()
            return '', None

    def drop_cached_content(self, url: str):
        with self._lock:
            self._uncommit_content_cache.pop(url, None)


# ------------------------------------------------------------------------------------------------------------------

class PrefixLogger:
    def __init__(self, logger: Logger, prefix):
        self.logger = logger
        self.prefix = prefix

    def debug(self, message):
        self.logger.debug(f"{self.prefix} {message}")

    def info(self, message):
        self.logger.info(f"{self.prefix} {message}")

    def warning(self, message):
        self.logger.warning(f"{self.prefix} {message}")

    def error(self, message):
        self.logger.error(f"{self.prefix} {message}")

    def critical(self, message):
        self.logger.critical(f"{self.prefix} {message}")


# ------------------------------------------------------------------------------------------------------------------

class CrawlContext:
    def __init__(self,
                 flow_name: str,
                 i_hub_url: str,
                 collector_token: str,
                 crawler_governor: GovernanceManager,
                 error_threshold: int = DEFAULT_CRAWL_ERROR_THRESHOLD,
                 logger: Logger = None
                 ):
        self.flow_name = flow_name
        self.i_hub_url = i_hub_url
        self.crawler_governor = crawler_governor
        self.collector_token = collector_token or DEFAULT_COLLECTOR_TOKEN
        self.error_threshold = error_threshold
        self.logger = PrefixLogger(logger or
                                   get_tls_logger(__name__) or
                                   logging.getLogger(__name__), f'[{flow_name}]:')
        self.crawl_cache = CrawlCache()
        self._submit_collected_data = post_collected_intelligence

    def is_url_in_cache(self, url: str):
        return self.crawl_cache.is_in_cache(url)

    def check_get_cached_data(self, url: str)-> CollectedData:
        return self.crawl_cache.pop_content(url)

    def submit_collected_data(
            self,
            group: str,
            collected_data: CollectedData,
            cache_on_error: bool = True
    ):
        collected_data.token = self.collector_token

        if self._submit_collected_data:
            self.logger.info(f"Submit collected data to: {self.i_hub_url}")
            result = self._submit_collected_data(self.i_hub_url, collected_data, 10)

            if result.get('status', 'success') == 'error':
                if cache_on_error:
                    # Only cache on submission error.
                    self.crawl_cache.cache_content(
                        collected_data.informant, (collected_data, group)   # <- Cached data is packed here.
                    )
                raise CrawlSession.Cached('commit_error')
        else:
            self.logger.warning(f'no method to submit collected data, data dropped.')

        self.logger.debug(f'Article finished.')

    def submit_cached_data(self, limit: int = -1):
        count = 0
        while (limit < 0) or (count < limit):
            url, content = self.crawl_cache.pop_random_item()
            if not content:
                break
            collected_data, group = content
            with self.crawler_governor.transaction(url, group) as task:
                try:
                    self.submit_collected_data(group, collected_data)
                    task.success(state_msg='Cached data submitted.')
                except CrawlSession.Flow:
                    raise           # Handle by context
                except Exception as e:
                    self.handle_process_exception(task, e)
                finally:
                    count += 1
        if count:
            self.logger.info(f"Process cached data for {self.flow_name}, count: {count}.")

    def handle_process_exception(self, task: CrawlSession, e: Exception):
        if isinstance(e, ProcessSkip):
            task.skip(e.reason)
            self.logger.debug('Article skipped.')

        elif isinstance(e, ProcessIgnore):
            task.ignore()
            self.logger.debug('Article ignored.')

        elif isinstance(e, ProcessProblem):
            if e.problem == 'fetch_error':
                task.fail_temp(state_msg='Fetch error')
            elif e.problem in ['commit_error']:
                # Just ignore because there will be a retry at next loop.
                task.cached()
            else:
                task.fail_perm(state_msg=f"Task {task.group_path} got unexpected ProcessProblem reason: {e.problem}")

        else:
            task.fail_perm(state_msg=str(e))
            self.logger.error(f"Task {task.group_path} got unexpected exception: {str(e)}")
            print(format_exception_with_traceback(e))

    # def check_raise_url_status(self, article_link: str, crawl_record: CrawlRecord, levels: str | List[str] = ''):
    #     """
    #     This function returns nothing. If everything is OK, this function will pass through otherwise raise exceptions.
    #     :param article_link: The link to be checked.
    #     :param crawl_record: The crawl record instance.
    #     :param levels: Levels of logging and record.
    #     :return: None
    #     """
    #     full_levels = self._full_levels(levels)
    #     url_status = crawl_record.get_url_status(article_link, from_db=False)
    #
    #     if url_status >= STATUS_SUCCESS:
    #         raise ProcessSkip('already exists', article_link, leveling=full_levels)
    #     elif url_status <= STATUS_UNKNOWN:
    #         pass  # <- Process going on here
    #     elif url_status == STATUS_ERROR:
    #         url_error_count = crawl_record.get_error_count(article_link, from_db=False)
    #         if url_error_count < 0:
    #             raise ProcessProblem('db_error', article_link, leveling=full_levels)
    #         if url_error_count >= self.error_threshold:
    #             raise ProcessSkip('max retry exceed', article_link, leveling=full_levels)
    #         else:
    #             pass  # <- Process going on here
    #     else:  # STATUS_DB_ERROR
    #         raise ProcessProblem('db_error', article_link, leveling=full_levels)
    #
    #     # ----- Also keep old mechanism checking to make it compatible -----
    #     if has_url(article_link):
    #         raise ProcessSkip('already exists', article_link, leveling=full_levels)

    # @staticmethod
    # def wait_interruptibly(total_duration_s: int, stop_event: threading.Event) -> bool:
    #     """
    #     Waits for the specified duration while periodically checking for the stop_event.
    #
    #     Returns True if the full duration was reached, False if the event was set early.
    #     """
    #     remaining = total_duration_s
    #
    #     CHECK_INTERVAL_S = 5
    #
    #     while remaining > 0 and not stop_event.is_set():
    #         sleep_time = min(CHECK_INTERVAL_S, remaining)
    #         time.sleep(sleep_time)
    #         remaining -= sleep_time
    #
    #     return remaining <= 0
