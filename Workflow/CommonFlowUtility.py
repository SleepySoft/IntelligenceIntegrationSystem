import time
import logging
import traceback
from logging import Logger

import urllib3
import threading
from typing import Dict, Tuple, Optional, Any

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
    """Thread-safe cache for crawled web content with group support."""

    def __init__(self):
        self._lock = threading.Lock()  # Thread safety lock
        # Two-level cache: group -> {url: content}
        self._uncommit_content_cache: Dict[str, Dict[str, Any]] = defaultdict(dict)
        # Reverse mapping: url -> group for quick lookup
        self._url_to_group: Dict[str, str] = {}

    def cache_len(self) -> int:
        """Return total number of cached items across all groups."""
        with self._lock:
            return sum(len(url_dict) for url_dict in self._uncommit_content_cache.values())

    def cache_content(self, group: str, url: str, content: Any) -> None:
        """Cache content with group and URL as composite key."""
        with self._lock:
            # If URL already exists in another group, remove it first
            if url in self._url_to_group:
                old_group = self._url_to_group[url]
                if old_group != group and url in self._uncommit_content_cache[old_group]:
                    del self._uncommit_content_cache[old_group][url]
                    # Clean up empty group
                    if not self._uncommit_content_cache[old_group]:
                        del self._uncommit_content_cache[old_group]

            # Cache the new content
            self._uncommit_content_cache[group][url] = content
            self._url_to_group[url] = group

    def pop_content(self, url: str) -> Optional[Tuple[str, str, Any]]:
        """Remove and return content for specific URL (group is determined automatically)."""
        with self._lock:
            if url not in self._url_to_group:
                return None

            group = self._url_to_group[url]
            if group in self._uncommit_content_cache and url in self._uncommit_content_cache[group]:
                content = self._uncommit_content_cache[group].pop(url)
                del self._url_to_group[url]

                # Clean up empty group
                if not self._uncommit_content_cache[group]:
                    del self._uncommit_content_cache[group]

                return group, url, content
            return None

    def pop_random_item(self) -> Optional[Tuple[str, str, Any]]:
        """Remove and return a random item with its group and URL."""
        with self._lock:
            if not self._uncommit_content_cache:
                return None

            # Select random group
            group = random.choice(list(self._uncommit_content_cache.keys()))
            if not self._uncommit_content_cache[group]:
                # Clean up empty group
                del self._uncommit_content_cache[group]
                return self.pop_random_item()  # Try again

            # Select random URL from group
            url = random.choice(list(self._uncommit_content_cache[group].keys()))
            content = self._uncommit_content_cache[group].pop(url)
            del self._url_to_group[url]

            # Clean up empty group
            if not self._uncommit_content_cache[group]:
                del self._uncommit_content_cache[group]

            return group, url, content

    def get_group_urls(self, group: str) -> list:
        """Return all URLs cached under the specified group."""
        with self._lock:
            return list(self._uncommit_content_cache.get(group, {}).keys())

    def drop_cached_content(self, url: str) -> None:
        """Remove specific content from cache without returning it (group is determined automatically)."""
        with self._lock:
            if url not in self._url_to_group:
                return

            group = self._url_to_group[url]
            if group in self._uncommit_content_cache and url in self._uncommit_content_cache[group]:
                del self._uncommit_content_cache[group][url]
                del self._url_to_group[url]

                # Clean up empty group
                if not self._uncommit_content_cache[group]:
                    del self._uncommit_content_cache[group]

    def get_group_for_url(self, url: str) -> Optional[str]:
        """Get the group for a specific URL."""
        with self._lock:
            return self._url_to_group.get(url)

    def clear_group(self, group: str) -> None:
        """Clear all cached content for a specific group."""
        with self._lock:
            if group in self._uncommit_content_cache:
                # Remove URLs from reverse mapping
                for url in self._uncommit_content_cache[group]:
                    if url in self._url_to_group:
                        del self._url_to_group[url]

                # Remove the group
                del self._uncommit_content_cache[group]


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

        # self.crawl_record = CrawlRecord(['crawl_record', flow_name])
        # self.crawl_statistics = CrawlStatistics()
        self.crawl_cache = CrawlCache()

        self._submit_collected_data = post_collected_intelligence

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

    def check_get_cached_data(self, url: str = None)-> CollectedData:
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
                    self.crawl_cache.cache_content(group, collected_data.informant, collected_data)
                raise CrawlSession.Cached('commit_error')
        else:
            self.logger.warning(f'no method to submit collected data, data dropped.')

        self.logger.debug(f'Article finished.')

    def submit_cached_data(self, limit: int = -1):
        count = 0
        while (limit < 0) or (count < limit):
            url, collected_data = self.crawl_cache.pop_random_item()
            if not collected_data:
                break
            group_path = collected_data.temp_data.get('group_path', '')
            with self.crawler_governor.transaction(url, group_path) as task:
                try:
                    self.submit_collected_data(collected_data, task)
                    task.success(state_msg='Cached data submitted.')
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


    @staticmethod
    def wait_interruptibly(total_duration_s: int, stop_event: threading.Event) -> bool:
        """
        Waits for the specified duration while periodically checking for the stop_event.

        Returns True if the full duration was reached, False if the event was set early.
        """
        remaining = total_duration_s

        CHECK_INTERVAL_S = 5

        while remaining > 0 and not stop_event.is_set():
            sleep_time = min(CHECK_INTERVAL_S, remaining)
            time.sleep(sleep_time)
            remaining -= sleep_time

        return remaining <= 0
