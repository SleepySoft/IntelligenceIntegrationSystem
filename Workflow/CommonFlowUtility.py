import threading

import time
import logging
from logging import Logger

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
from Tools.ContentHistory import save_content
from Streamer.ToFileAndHistory import to_file_and_history
from Tools.CrawlRecord import CrawlRecord, STATUS_ERROR, STATUS_SUCCESS, STATUS_DB_ERROR, STATUS_UNKNOWN, STATUS_IGNORED
from Tools.CrawlStatistics import CrawlStatistics
from Tools.ProcessCotrolException import ProcessSkip, ProcessError, ProcessTerminate, ProcessProblem, ProcessIgnore
from Tools.RSSFetcher import FeedData


DEFAULT_CRAWL_ERROR_THRESHOLD = 3


# ------------------------------------------------------------------------------------------------------------------

class CrawlCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._uncommit_content_cache = { }

    def cache_len(self) -> int:
        return len(self._uncommit_content_cache)

    def cache_content(self, url: str, content: any):
        with self._lock:
            if url not in self._uncommit_content_cache:
                self._uncommit_content_cache[url] = content

    def pop_cached_content(self, url: str) -> any:
        with self._lock:
            return self._uncommit_content_cache.pop(url, None) \
                if url else \
                self._uncommit_content_cache.popitem()

    def drop_cached_content(self, url: str):
        with self._lock:
            if url in self._uncommit_content_cache:
                del self._uncommit_content_cache[url]


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
                 error_threshold: int = DEFAULT_CRAWL_ERROR_THRESHOLD,
                 logger: Logger = None
                 ):
        self.flow_name = flow_name
        self.i_hub_url = i_hub_url
        self.collector_token = collector_token or DEFAULT_COLLECTOR_TOKEN
        self.error_threshold = error_threshold
        self.logger = PrefixLogger(logger or
                                   get_tls_logger(__name__) or
                                   logging.getLogger(__name__), f'[{flow_name}]:')

        self.crawl_record = CrawlRecord(['crawl_record', flow_name])
        self.crawl_statistics = CrawlStatistics()
        self.crawl_cache = CrawlCache()

        self._submit_collected_data = post_collected_intelligence

    def check_raise_url_status(self, article_link: str, crawl_record: CrawlRecord, levels: str | List[str] = ''):
        """
        This function returns nothing. If everything is OK, this function will pass through otherwise raise exceptions.
        :param article_link: The link to be checked.
        :param crawl_record: The crawl record instance.
        :param levels: Levels of logging and record.
        :return: None
        """
        full_levels = self._full_levels(levels)
        url_status = crawl_record.get_url_status(article_link, from_db=False)

        if url_status >= STATUS_SUCCESS:
            raise ProcessSkip('already exists', article_link, leveling=full_levels)
        elif url_status <= STATUS_UNKNOWN:
            pass  # <- Process going on here
        elif url_status == STATUS_ERROR:
            url_error_count = crawl_record.get_error_count(article_link, from_db=False)
            if url_error_count < 0:
                raise ProcessProblem('db_error', article_link, leveling=full_levels)
            if url_error_count >= self.error_threshold:
                raise ProcessSkip('max retry exceed', article_link, leveling=full_levels)
            else:
                pass  # <- Process going on here
        else:  # STATUS_DB_ERROR
            raise ProcessProblem('db_error', article_link, leveling=full_levels)

        # ----- Also keep old mechanism checking to make it compatible -----
        if has_url(article_link):
            raise ProcessSkip('already exists', article_link, leveling=full_levels)

    def check_get_cached_data(self, url: str = None) -> CollectedData:
        return self.crawl_cache.pop_cached_content(url)

    def submit_collected_data(self,
                              collected_data: CollectedData,
                              levels: str | List[str] = '',
                              cache_on_error: bool = True,
                              persists: bool = True
                              ):
        full_levels = self._full_levels(levels)
        collected_data.token = self.collector_token

        # -------------------------------- Submit Data Here --------------------------------

        if self._submit_collected_data:
            result = self._submit_collected_data(self.i_hub_url, collected_data, 10)
            if result.get('status', 'success') == 'error':
                if cache_on_error:
                    # Only cache on submission error.
                    self.crawl_cache.cache_content(collected_data.informant, collected_data)
                raise ProcessProblem('commit_error', leveling=full_levels)
        else:
            self.logger.warning(f'no method to submit collected data.')

        # ------------------------------- Persists Data Here -------------------------------

        if persists and collected_data.content:
            success, file_path = save_content(
                collected_data.informant,
                collected_data.content,
                collected_data.title,
                self.flow_name,
                '.md'
            )
            if not success:
                self.logger.error(f'Save content {file_path} fail.')

        # --------------------------- Record and Statistics Here ---------------------------

        self.crawl_record.record_url_status(collected_data.informant, STATUS_SUCCESS)
        self.crawl_statistics.sub_item_log(full_levels, collected_data.informant, 'success')

        self.logger.debug(f'Article finished.')

    def handle_process_exception(self, e: Exception):
        try:
            raise e
        except ProcessSkip:
            print('*', end='', flush=True)
            self.logger.debug(f'Article skipped.')

        except ProcessIgnore as e:
            print('o', end='', flush=True)
            self.logger.debug(f'Article ignored.')
            self.crawl_record.record_url_status(e.item, STATUS_IGNORED)
            self.crawl_statistics.sub_item_log(e.data.get('leveling', [self.flow_name]), e.item, e.reason)

        except ProcessProblem as e:
            if e.problem == 'db_error':
                # DB error, not content error, just ignore and retry at next loop.
                self.logger.error('Crawl record DB Error.')
            elif e.problem in ['fetch_error', 'persists_error', 'commit_error']:
                # If just commit error, just retry with cache.
                # Persists error, actually once we're starting use CrawRecord. We don't need this anymore
                print('x', end='', flush=True)
                self.logger.error(e.problem)
                if e.problem != 'commit_error':
                    self.crawl_record.increment_error_count(e.item)
                self.crawl_statistics.sub_item_log(e.data.get('leveling', [self.flow_name]), e.item, e.problem)
            else:
                pass

    # ------------------------------------------------------------------------------------------------------------------

    def _full_levels(self, levels: str | List[str] = '') -> List[str]:
        full_levels = [self.flow_name]
        if levels:
            if isinstance(levels, str):
                full_levels.append(levels)
            elif isinstance(levels, (list, tuple, set)):
                full_levels += list(levels)
        return full_levels



