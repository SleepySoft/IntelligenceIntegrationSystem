# CrawlPipeline.py

import datetime
import traceback
from typing import List, Optional, Callable, Any, Tuple, Dict

# Import interfaces and base classes
from IntelligenceCrawler.Fetcher import Fetcher
from IntelligenceCrawler.Extractor import IExtractor, ExtractionResult
from IntelligenceCrawler.Discoverer import IDiscoverer


class CrawlPipeline:
    """
    A stateful pipeline that encapsulates the 3-stage process of
    Discovering channels, Fetching articles, and Extracting content.
    """

    def __init__(self,
                 d_fetcher: Fetcher,
                 discoverer: IDiscoverer,
                 e_fetcher: Fetcher,
                 extractor: IExtractor,
                 log_callback: Callable[..., None] = print):
        """
        Initializes the pipeline with all required components.

        Args:
            d_fetcher: Fetcher instance for the Discoverer.
            discoverer: IDiscoverer instance.
            e_fetcher: Fetcher instance for the Extractor.
            extractor: IExtractor instance.
            log_callback: A function (like print or a GUI logger) to send logs to.
        """
        self.d_fetcher = d_fetcher
        self.discoverer = discoverer
        self.e_fetcher = e_fetcher
        self.extractor = extractor
        self.log = log_callback

        # --- State Properties ---
        self.channels: List[str] = []
        self.contents: List[Tuple[str, bytes]] = []
        self.articles: List[ExtractionResult] = []

    def shutdown(self):
        """Gracefully closes both fetcher instances."""
        self.log("--- 5. Shutting down fetchers ---")
        try:
            if self.d_fetcher: self.d_fetcher.close()
        except Exception as e:
            self.log(f"[Error] Failed to close discovery fetcher: {e}")

        try:
            # Avoid closing the same fetcher twice if they are the same instance
            if self.e_fetcher and self.e_fetcher is not self.d_fetcher:
                self.e_fetcher.close()
        except Exception as e:
            self.log(f"[Error] Failed to close extraction fetcher: {e}")

    def discover_channels(self,
                          entry_point_urls: List[str],
                          start_date: Optional[datetime.datetime] = None,
                          end_date: Optional[datetime.datetime] = None) -> List[str]:
        """
        Step 1: Discovers all channels from a list of entry point URLs.
        Clears all internal state.
        """
        self.log(f"--- 1. Discovering Channels from {len(entry_point_urls)} entry point(s) ---")

        # Clear all previous state
        self.channels.clear()
        self.contents.clear()
        self.articles.clear()

        for url in entry_point_urls:
            self.log(f"Scanning entry point: {url}")
            try:
                channels_found = self.discoverer.discover_channels(
                    entry_point_url=url,
                    start_date=start_date,
                    end_date=end_date
                )
                self.channels.extend(channels_found)
                self.log(f"Found {len(channels_found)} channels from this entry point.")
            except Exception as e:
                self.log(f"[Error] Failed to discover from {url}: {e}\n{traceback.format_exc()}")

        # De-duplicate the list while preserving order
        self.channels = list(dict.fromkeys(self.channels))
        self.log(f"Found {len(self.channels)} unique channels in total.")
        return self.channels

    def discover_articles(self,
                          channel_filter: Optional[Callable[[str], bool]] = None) -> List[Tuple[str, bytes]]:
        """
        Step 2: Discovers article URLs from channels and fetches their content.
        Populates self.contents.
        """
        self.log(f"--- 2. Discovering & Fetching Articles from {len(self.channels)} Channels ---")
        self.contents.clear()
        self.articles.clear()

        for channel_url in self.channels:
            if channel_filter and not channel_filter(channel_url):
                self.log(f"Skipping channel (filtered): {channel_url}")
                continue

            self.log(f"Processing Channel: {channel_url}")
            try:
                articles_in_channel = self.discoverer.get_articles_for_channel(channel_url)
                self.log(f"Found {len(articles_in_channel)} articles in channel.")

                for article_url in articles_in_channel:
                    self.log(f"Fetching: {article_url}")
                    content = self.e_fetcher.get_content(article_url)
                    if not content:
                        self.log(f"Skipped (no content): {article_url}")
                        continue
                    # Store for the extraction step
                    self.contents.append((article_url, content))
            except Exception as e:
                self.log(f"[Error] Failed to process channel {channel_url}: {e}\n{traceback.format_exc()}")

        self.log(f"Fetched {len(self.contents)} article contents.")
        return self.contents

    def extract_articles(self,
                         content_handler: Optional[Callable[[ExtractionResult], None]] = None,
                         error_handler: Optional[Callable[[str, Exception], None]] = None,
                         **extractor_kwargs: Any) -> List[ExtractionResult]:
        """
        Step 3: Extracts content from all fetched articles.
        Populates self.articles and calls optional handlers.
        """
        self.log(f"--- 3. Extracting {len(self.contents)} Articles ---")
        self.articles.clear()

        for article_url, content in self.contents:
            try:
                result = self.extractor.extract(content, article_url, **extractor_kwargs)
                self.articles.append(result)  # Store the final result

                if content_handler:
                    content_handler(result)  # Pass full result to handler
            except Exception as e:
                self.log(f"[Error] Failed to extract {article_url}: {e}")
                if error_handler:
                    error_handler(article_url, e)  # Pass URL and exception

        self.log(f"Extracted {len(self.articles)} articles successfully.")
        return self.articles
