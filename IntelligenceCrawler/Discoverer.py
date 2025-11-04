import re
import datetime
import logging
import traceback
import xml.etree.ElementTree as ET
from collections import deque
from usp.tree import sitemap_from_str
from urllib.parse import urlparse, urljoin

# --- Core Imports ---
from abc import ABC, abstractmethod
from typing import Set, List, Dict, Any, Optional, Deque

# --- RSS/HTML Parsing Imports ---
import feedparser
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

# --- Date Imports (for interface compatibility) ---
try:
    from dateutil.parser import parse as date_parse
except ImportError:
    print("!!! IMPORT ERROR: 'python-dateutil' not found.")
    print("!!! Please install it for date filtering: pip install python-dateutil")
    date_parse = None

logger = logging.getLogger(__name__)


class IDiscoverer(ABC):
    """
    Abstract base class for a discovery component.
    (发现组件的抽象基类)

    Its role is to find "channels" (like leaf sitemaps or RSS feeds)
    and then extract individual article URLs from those channels.
    It relies on an injected Fetcher for all network operations.
    (它的职责是找到“频道”(如叶子sitemap或RSS feed)，
     然后从这些频道中提取单独的文章URL。
     它依赖注入的 Fetcher 来执行所有网络操作。)
    """

    def __init__(self, fetcher: "Fetcher", verbose: bool = True):
        """
        Initializes the discoverer.
        (初始化发现器)

        :param fetcher: An instance of a Fetcher implementation.
        :param verbose: Toggles detailed logging.
        """
        self.fetcher = fetcher
        self.verbose = verbose
        self.log_messages: List[str] = []

    @abstractmethod
    def _log(self, message: str, indent: int = 0):
        """
        Provides a unified logging mechanism.
        (提供统一的日志记录机制)

        Note: We make this abstract so concrete classes *must*
        implement it, ensuring logging is available.
        (我们将其设为抽象，因此具体类*必须*实现它，以确保日志记录可用。)
        """
        pass

    @abstractmethod
    def discover_channels(self,
                          entry_point_url: str,
                          start_date: Optional[datetime.datetime] = None,
                          end_date: Optional[datetime.datetime] = None
                          ) -> List[str]:
        """
        Stage 1: Discovers all "channels" (e.g., leaf sitemaps, RSS feeds)
        from a main entry point (e.g., a homepage).
        (阶段1：从主入口点（例如主页）发现所有“频道”（例如叶子sitemap、RSS feed）)

        :param entry_point_url: The starting URL (e.g., https://example.com)
        :param start_date: (Optional) Filter to include only channels
                           relevant *after* this date.
        :param end_date: (Optional) Filter to include only channels
                         relevant *before* this date.
        :return: A list of string URLs, each representing a "channel"
                 that contains article links.
        """
        pass

    @abstractmethod
    def get_articles_for_channel(self, channel_url: str) -> List[str]:
        """
        Stage 2: Fetches and parses a single "channel" URL (found in Stage 1)
        to extract all individual article URLs it contains.
        (阶段2：获取并解析在阶段1中找到的单个“频道”URL，
         以提取其包含的所有单独的文章URL。)

        :param channel_url: The URL of a single channel
                            (e.g., a leaf sitemap or an RSS feed URL).
        :return: A list of string URLs for individual articles.
        """
        pass

    def get_content_str(self, url: str) -> str:
        """
        Helper to get raw content as a string for display or debugging.
        (辅助函数：获取原始内容的字符串用于显示或调试。)

        This can be a concrete method in the base class as it only
        depends on the fetcher.
        (这可以是基类中的一个具体方法，因为它只依赖于 fetcher。)
        """
        self.log_messages.clear()
        self._log(f"Fetching raw content for: {url}")
        content = self.fetcher.get_content(url)
        if content:
            try:
                return content.decode('utf-8', errors='ignore')
            except Exception as e:
                self._log(f"Error decoding content: {e}")
                return f"Error decoding content: {e}"
        return f"Failed to fetch content from {url}"


class SitemapDiscoverer(IDiscoverer):
    """
    Discovers articles by parsing sitemap.xml files.
    (通过解析 sitemap.xml 文件发现文章。)

    Requires a 'Fetcher' instance to be injected upon initialization.
    All network I/O is delegated to self.fetcher.

    v3.7 (Refactor): Now includes date filtering to avoid processing
    stale sitemap indexes.
    """
    NAMESPACES = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

    def __init__(self, fetcher: "Fetcher", verbose: bool = True):
        """
        Initializes the sitemap discoverer.
        (初始化 sitemap 发现器。)
        :param fetcher: An instance of a class that implements the Fetcher ABC.
        :param verbose: Whether to print detailed log messages.
        """
        super().__init__(fetcher, verbose)

        # --- State Properties ---
        self.all_article_urls: Set[str] = set()
        self.leaf_sitemaps: Set[str] = set()
        self.to_process_queue: Deque[str] = deque()
        self.processed_sitemaps: Set[str] = set()

        # --- NEW: Check for dateutil library ---
        if not date_parse:
            self._log("[Warning] 'python-dateutil' not found. Date filtering will be disabled.")

    def _log(self, message: str, indent: int = 0):
        """Unified logging function."""
        log_msg = f"{' ' * (indent * 4)}{message}"
        self.log_messages.append(log_msg)
        if self.verbose:
            print(log_msg)

    def _discover_sitemap_entry_points(self, homepage_url: str) -> List[str]:
        """Step 1 (Internal): Automatically discover sitemap entry points."""
        self._log(f"Auto-discovering sitemap entry points for {homepage_url}...")
        try:
            parsed_home = urlparse(homepage_url)
            base_url = f"{parsed_home.scheme}://{parsed_home.netloc}"
        except Exception as e:
            self._log(f"[Error] Could not parse homepage URL: {e}")
            return []

        # Path 1: Check robots.txt (Preferred)
        robots_url = urljoin(base_url, '/robots.txt')
        self._log(f"Checking robots.txt: {robots_url}", 1)
        robots_content_bytes = self.fetcher.get_content(robots_url)

        sitemap_urls = []
        if robots_content_bytes:
            try:
                sitemap_urls = re.findall(
                    r"^Sitemap:\s*(.+)$",
                    robots_content_bytes.decode('utf-8', errors='ignore'),
                    re.IGNORECASE | re.MULTILINE
                )
                sitemap_urls = [url.strip() for url in sitemap_urls]
                if sitemap_urls:
                    self._log(f"Found {len(sitemap_urls)} sitemap(s) in robots.txt: {sitemap_urls}", 1)
                    return sitemap_urls
            except Exception as e:
                self._log(f"Error parsing robots.txt: {e}", 1)

        # Path 2: Guess default paths (Fallback)
        self._log("No sitemaps found in robots.txt. Guessing default paths...", 1)
        return [
            urljoin(base_url, '/sitemap_index.xml'),
            urljoin(base_url, '/sitemap.xml')
        ]

    # --- Date parsing and checking helper ---
    def _parse_and_check_date(self,
                              lastmod_str: Optional[str],
                              start_date: Optional[datetime.datetime],
                              end_date: Optional[datetime.datetime]) -> bool:
        """
        Checks if a sitemap's lastmod date is within the desired range.
        Returns True if it should be processed, False if it should be skipped.
        """

        # Rule 1: If no date library, we can't filter. Process everything.
        if not date_parse:
            return True

            # Rule 2: If no date limits are set by the user, always process.
        if not start_date and not end_date:
            return True

        # Rule 3: If the sitemap has no <lastmod>, process it (our fallback).
        if not lastmod_str:
            self._log("      > No <lastmod> date found. Including by default.", 3)
            return True

        try:
            # Attempt to parse the date string (e.g., "2025-11-01T18:23:17+00:00")
            sitemap_date = date_parse(lastmod_str)

            # --- Timezone Handling (CRITICAL for correct comparison) ---
            # Make sure sitemap_date is timezone-aware (assume UTC if naive)
            if sitemap_date.tzinfo is None:
                sitemap_date = sitemap_date.replace(tzinfo=datetime.timezone.utc)

            # Make sure start_date is timezone-aware (assume UTC if naive)
            start_date_aware = start_date
            if start_date and start_date.tzinfo is None:
                start_date_aware = start_date.replace(tzinfo=datetime.timezone.utc)

            # Make sure end_date is timezone-aware (assume UTC if naive)
            end_date_aware = end_date
            if end_date and end_date.tzinfo is None:
                end_date_aware = end_date.replace(tzinfo=datetime.timezone.utc)
            # --- End Timezone Handling ---

            # Rule 4: Check against start_date
            if start_date_aware and sitemap_date < start_date_aware:
                self._log(
                    f"      > SKIPPING: Date {sitemap_date.date()} is older than start date {start_date_aware.date()}",
                    3)
                return False

            # Rule 5: Check against end_date
            if end_date_aware and sitemap_date > end_date_aware:
                self._log(
                    f"      > SKIPPING: Date {sitemap_date.date()} is newer than end date {end_date_aware.date()}", 3)
                return False

            # Rule 6: It's within range
            self._log(f"      > Date {sitemap_date.date()} is within range. Including.", 3)
            return True

        except Exception as e:
            # If parsing fails (e.g., "invalid date format"), process it just to be safe.
            self._log(f"      > Warning: Could not parse date '{lastmod_str}'. Error: {e}. Including by default.", 3)
            return True

    # --- _parse_sitemap_xml now returns richer data ---
    def _parse_sitemap_xml(self, xml_content: bytes, sitemap_url: str) -> Dict[str, List[Any]]:
        """
        Parses Sitemap XML content with a fallback mechanism.

        Returns a dict:
        {
            'pages': List[str],  // List of page URLs
            'sub_sitemaps': List[Dict[str, Optional[str]]] // List of {'loc': url, 'lastmod': date_str}
        }
        """
        pages: List[str] = []
        # --- UPDATED: sub_sitemaps is now a list of dicts ---
        sub_sitemaps: List[Dict[str, Optional[str]]] = []

        try:
            self._log("    Trying to parse with [ultimate-sitemap-parser]...", 1)
            parsed_sitemap = sitemap_from_str(xml_content.decode('utf-8', errors='ignore'))

            for page in parsed_sitemap.all_pages():
                pages.append(page.url)

            # --- UPDATED: Extract lastmod along with loc ---
            for sub_sitemap in parsed_sitemap.all_sub_sitemaps():
                lastmod_str = sub_sitemap.lastmod.isoformat() if sub_sitemap.lastmod else None
                sub_sitemaps.append({
                    'loc': sub_sitemap.url,
                    'lastmod': lastmod_str
                })
            self._log(f"    [USP Success] Found {len(pages)} pages and {len(sub_sitemaps)} sub-sitemaps.", 1)

        except Exception as e:
            self._log(f"    [USP Failed] Library parsing error: {e}", 1)
            self._log("    --> Initiating [Manual ElementTree] fallback...", 1)
            try:
                root = ET.fromstring(xml_content)
                index_nodes = root.findall('ns:sitemap', self.NAMESPACES)

                if index_nodes:
                    # --- UPDATED: Extract lastmod along with loc ---
                    for node in index_nodes:
                        loc_node = node.find('ns:loc', self.NAMESPACES)
                        lastmod_node = node.find('ns:lastmod', self.NAMESPACES)

                        loc_text = loc_node.text if loc_node is not None and loc_node.text else None
                        lastmod_text = lastmod_node.text if lastmod_node is not None and lastmod_node.text else None

                        if loc_text:
                            sub_sitemaps.append({
                                'loc': loc_text,
                                'lastmod': lastmod_text
                            })
                    self._log(f"    [Manual Fallback] Found {len(sub_sitemaps)} sub-sitemaps.", 1)

                url_nodes = root.findall('ns:url', self.NAMESPACES)
                if url_nodes:
                    for node in url_nodes:
                        loc = node.find('ns:loc', self.NAMESPACES)
                        if loc is not None and loc.text:
                            pages.append(loc.text)
                    self._log(f"    [Manual Fallback] Found {len(pages)} pages.", 1)

                if not index_nodes and not url_nodes:
                    self._log("    [Manual Fallback] Failed: No <sitemap> or <url> tags found.", 1)
            except ET.ParseError as xml_e:
                self._log(f"    [Manual Fallback] Failed: Could not parse XML. Error: {xml_e}", 1)

        return {'pages': pages, 'sub_sitemaps': sub_sitemaps}

    def discover_channels(self,
                          homepage_url: str,
                          start_date: Optional[datetime.datetime] = datetime.datetime.now() - datetime.timedelta(days=7),
                          end_date: Optional[datetime.datetime] = datetime.datetime.now()) -> List[str]:
        """
        STAGE 1: Discover all "channels" (leaf sitemaps containing articles).

        :param homepage_url: The root URL of the website.
        :param start_date: (Optional) The earliest date to include sitemaps from.
        :param end_date: (Optional) The latest date to include sitemaps from.
        """
        self._log(f"--- STAGE 1: Discovering Channels for {homepage_url} ---")
        if start_date or end_date:
            self._log(
                f"Filtering sitemaps between: {start_date.date() if start_date else 'Beginning'} and {end_date.date() if end_date else 'Today'}")

        self.log_messages.clear()
        self.leaf_sitemaps.clear()
        self.to_process_queue.clear()
        self.processed_sitemaps.clear()

        initial_sitemaps = self._discover_sitemap_entry_points(homepage_url)
        if not initial_sitemaps:
            self._log("Could not find any sitemap entry points.")
            return []

        self.to_process_queue.extend(initial_sitemaps)

        while self.to_process_queue:
            # --- UPDATED: Limit queue size to prevent infinite loops on bad sites ---
            if len(self.to_process_queue) > 5000:
                self._log("[Error] Queue size exceeds 5000. Aborting to prevent infinite loop.")
                break

            sitemap_url = self.to_process_queue.popleft()
            if sitemap_url in self.processed_sitemaps:
                continue
            self.processed_sitemaps.add(sitemap_url)


            # 在抓取(fetch)之前，先检查 URL 字符串本身
            if not self._check_url_against_date_range(sitemap_url, start_date, end_date):
                continue

            self._log(f"\n--- Analyzing index: {sitemap_url} ---")
            xml_content = self.fetcher.get_content(sitemap_url)

            # (Your debug print, you can remove this)
            # print('------------------------------------------ XML ------------------------------------------')
            # xml_text = xml_content.decode('utf-8') if xml_content else "NO CONTENT"
            # print(xml_text)
            # print('-----------------------------------------------------------------------------------------')

            if not xml_content:
                self._log("  Failed to fetch, skipping.", 1)
                continue

            parse_result = self._parse_sitemap_xml(xml_content, sitemap_url)

            # --- UPDATED: This is the core filtering logic ---
            if parse_result['sub_sitemaps']:
                self._log(f"  > Found {len(parse_result['sub_sitemaps'])} sub-indexes. Filtering by date...", 2)

                valid_sitemaps_to_queue = []
                for sitemap_info in parse_result['sub_sitemaps']:
                    loc = sitemap_info['loc']
                    lastmod = sitemap_info['lastmod']

                    self._log(f"    - Checking: {loc}", 3)

                    # Use the new helper function to decide
                    if self._parse_and_check_date(lastmod, start_date, end_date):
                        valid_sitemaps_to_queue.append(loc)

                self._log(
                    f"  > Queuing {len(valid_sitemaps_to_queue)} out of {len(parse_result['sub_sitemaps'])} sub-indexes.",
                    2)
                self.to_process_queue.extend(valid_sitemaps_to_queue)
            # --- END UPDATED BLOCK ---

            if parse_result['pages']:
                self._log(f"  > Found {len(parse_result['pages'])} pages. Marking as 'Channel'.", 2)
                # This is a leaf node, so we just add it.
                # The *date* of the sitemap file itself doesn't matter here,
                # only that it contains article URLs.
                self.leaf_sitemaps.add(sitemap_url)

        self._log(f"\nStage 1 Complete: Discovered {len(self.leaf_sitemaps)} total channels.")
        return list(self.leaf_sitemaps)

    # --- (get_articles_for_channel & get_xml_content_str are unchanged) ---
    def get_articles_for_channel(self, channel_url: str) -> List[str]:
        """
        Helper for Stage 2 (Lazy Loading): Gets pages for ONE specific channel.
        """
        self.log_messages.clear()
        self._log(f"--- STAGE 2: Fetching articles for {channel_url} ---")
        xml_content = self.fetcher.get_content(channel_url)
        if not xml_content:
            return []

        # Note: This *could* also be modified to filter articles by date
        # but for now it just returns all articles from the channel.
        parse_result = self._parse_sitemap_xml(xml_content, channel_url)
        self._log(f"  > Found {len(parse_result['pages'])} articles.")
        return parse_result['pages']

    def get_xml_content_str(self, url: str) -> str:
        """Helper to get raw XML as a string for display."""
        self.log_messages.clear()
        self._log(f"Fetching XML content for: {url}")
        content = self.fetcher.get_content(url)
        if content:
            try:
                return content.decode('utf-8', errors='ignore')
            except Exception as e:
                self._log(f"Error decoding XML: {e}")
                return f"Error decoding XML: {e}"
        return f"Failed to fetch content from {url}"

    def _check_url_against_date_range(self,
                                      sitemap_url: str,
                                      start_date: Optional[datetime.datetime],
                                      end_date: Optional[datetime.datetime]) -> bool:
        """
        [新功能] 检查 sitemap URL 字符串本身是否包含日期信息，并判断是否在范围内。
        返回 True (应该处理) 或 False (应该跳过)。
        """
        # 规则 1: 如果没有日期库或日期范围，无法过滤，必须处理。
        if not date_parse or (not start_date and not end_date):
            return True

        # 规则 2: 尝试从 URL 中匹配日期
        # 匹配: 2024-01-04 | 2025-November-1 | 2025 (必须紧跟 .xml)
        pattern = r"(\d{4}-\d{2}-\d{2})|(\d{4}-[A-Za-z]+-\d{1,2})|(\d{4})(?=\.xml)"
        match = re.search(pattern, sitemap_url)

        # 规则 3: URL 中没有可识别的日期，必须处理 (依赖后续的 lastmod)
        if not match:
            return True

        date_str = match.group(0)

        try:
            # --- 统一处理时区 (从 _parse_and_check_date 复制) ---
            start_date_aware = start_date
            if start_date and start_date.tzinfo is None:
                start_date_aware = start_date.replace(tzinfo=datetime.timezone.utc)

            end_date_aware = end_date
            if end_date and end_date.tzinfo is None:
                end_date_aware = end_date.replace(tzinfo=datetime.timezone.utc)
            # --- 时区处理结束 ---

            # 规则 4: 特殊处理纯年份 (例如 "2025")
            if len(date_str) == 4 and date_str.isdigit():
                year = int(date_str)
                # 该 URL 代表的开始时间 (e.g., 2025-01-01 00:00:00)
                sitemap_year_start = datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc)
                # 该 URL 代表的结束时间 (e.g., 2025-12-31 23:59:59)
                sitemap_year_end = datetime.datetime(year + 1, 1, 1, tzinfo=datetime.timezone.utc) - datetime.timedelta(
                    seconds=1)

                # 4a: 如果用户的开始日期在这一年的结束之后 (e.g., 2026-01-01)，跳过
                if start_date_aware and start_date_aware > sitemap_year_end:
                    self._log(
                        f"  > SKIPPING (URL): Year {date_str} is older than start date {start_date_aware.date()}", 1)
                    return False

                # 4b: 如果用户的结束日期在这一年的开始之前 (e.g., 2024-12-31)，跳过
                if end_date_aware and end_date_aware < sitemap_year_start:
                    self._log(
                        f"  > SKIPPING (URL): Year {date_str} is newer than end date {end_date_aware.date()}", 1)
                    return False

                # 4c: 年份有重叠，处理
                self._log(f"  > (URL) Year {date_str} overlaps with date range. Processing.", 1)
                return True

            # 规则 5: 处理标准日期 (YYYY-MM-DD 或 YYYY-Month-D)
            sitemap_date = date_parse(date_str)
            if sitemap_date.tzinfo is None:
                sitemap_date = sitemap_date.replace(tzinfo=datetime.timezone.utc)

            # 5a: 检查开始日期
            if start_date_aware and sitemap_date < start_date_aware:
                self._log(
                    f"  > SKIPPING (URL): Date {sitemap_date.date()} is older than start date {start_date_aware.date()}",
                    1)
                return False

            # 5b: 检查结束日期
            if end_date_aware and sitemap_date > end_date_aware:
                self._log(
                    f"  > SKIPPING (URL): Date {sitemap_date.date()} is newer than end date {end_date_aware.date()}", 1)
                return False

            # 5c: 在范围内
            self._log(f"  > (URL) Date {sitemap_date.date()} is within range. Processing.", 1)
            return True

        except Exception as e:
            # 解析失败，宁可抓错也别放过
            self._log(f"  > Warning: Could not parse date '{date_str}' from URL. Error: {e}. Processing anyway.", 1)
            return True


# =======================================================================
# == 2. RSS PARSING UTILITIES (From your RSS Fetcher file)
# =======================================================================

class RssMeta(BaseModel):
    """Pydantic model for RSS feed metadata."""
    title: str = ''  # The title of channel (maybe)
    link: str = ''  # Not the feed link. I have no idea.
    description: str = ''  # Description of this feed
    language: str = ''  # Like: zh-cn
    updated: object | None = None  # ?


class RssItem(BaseModel):
    """Pydantic model for a single RSS item/entry."""
    title: str  # The title of article
    link: str  # The link of article
    published: object | None  # Published time
    authors: list  # Authors but in most case it's empty
    description: str  # Description of this article
    guid: str  # In most case it's empty
    categories: list  # In most case it's empty
    media: object | None  # ......


class FeedData(BaseModel):
    """Pydantic model for the complete parsed feed data."""
    meta: RssMeta
    entries: List[RssItem]
    errors: List[str]
    fatal: bool


def sanitize_html(raw: str) -> str:
    """Strips HTML tags and returns clean text."""
    return BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)


def extract_media(entry) -> list:
    """Extracts media enclosures and media:content from a feed entry."""
    media = []
    # Process enclosure tags
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith(("image/", "video/", "audio/")):
            media.append({
                "url": enc["href"],
                "type": enc["type"],
                "length": enc.get("length", 0)
            })
    # Process media_content extensions
    for mc in entry.get("media_content", []):
        media.append({
            "url": mc["url"],
            "type": mc.get("type", "unknown"),
            "width": mc.get("width", 0),
            "height": mc.get("height", 0)
        })
    return media


def parse_feed(content: str) -> FeedData:
    """
    Parses RSS/Atom content string using feedparser and returns a FeedData object.

    :param content: The original RSS/Atom XML content string.
    :return: A FeedData object containing metadata, entries, and any errors.
    """
    errors = []
    try:
        parsed = feedparser.parse(content)

        if parsed.get("bozo", 0) == 1:
            exception = parsed.get("bozo_exception", Exception("Unknown parsing error"))
            errors.append(str(exception))
            logger.error(f'Feed XML parse fail: {str(exception)}')

        meta = RssMeta(
            title=parsed.feed.get("title", ""),
            link=parsed.feed.get("link", ""),
            description=parsed.feed.get("description", ""),
            language=parsed.feed.get("language", "zh-cn"),
            updated=parsed.feed.get("updated_parsed", None)
        )

        # Process article items
        entries = []
        for entry in parsed.entries:
            authors = []
            for author_data in entry.get("authors", []):
                if author := author_data.get("name", '').strip():
                    authors.append(author)
            item = RssItem(
                title=entry.get("title", "Untitled"),
                link=entry.get("link", ""),
                published=entry.get("published_parsed", entry.get("updated_parsed", None)),
                authors=authors,
                description=sanitize_html(entry.get("description", "")),
                guid=entry.get("id", ""),
                categories=entry.get("tags", []),
                media=extract_media(entry)
            )
            entries.append(item)

        feed_data = FeedData(
            meta=meta,
            entries=entries,
            errors=errors,
            fatal=False
        )
        return feed_data

    except Exception as e:
        error_text = f"Exception: {str(e)}"
        errors.append(error_text)
        logger.error(error_text, exc_info=True)

        return FeedData(
            meta=RssMeta(),
            entries=[],
            errors=errors,
            fatal=True
        )


# =======================================================================
# == 3. CONCRETE IMPLEMENTATION: RSSDiscoverer
# =======================================================================

class RSSDiscoverer(IDiscoverer):
    """
    Implements the IDiscoverer interface for finding articles via
    RSS and Atom feeds.

    Stage 1 (discover_channels): Scrapes a homepage to find <link> tags
    pointing to RSS/Atom feeds. **If given a feed URL directly, it
    returns that URL.**

    Stage 2 (get_articles_for_channel): Fetches a single RSS/Atom feed URL
    and parses it to extract all article links.
    """

    def __init__(self, fetcher: "Fetcher", verbose: bool = True):
        """
        Initializes the RSS discoverer.

        :param fetcher: An instance of a Fetcher implementation.
        :param verbose: Toggles detailed logging.
        """
        super().__init__(fetcher, verbose)
        self._log("Initialized RSSDiscoverer.")

        # Standard RSS/Atom MIME types to look for
        self.FEED_MIME_TYPES = {
            'application/rss+xml',
            'application/atom+xml',
            'application/xml',
            'text/xml',
            'application/feed+json',  # Also include JSON feeds
        }

    def _log(self, message: str, indent: int = 0):
        """
        Unified logging function.

        :param message: The log message.
        :param indent: The indentation level (multiplied by 4 spaces).
        """
        log_msg = f"{' ' * (indent * 4)}{message}"
        self.log_messages.append(log_msg)
        if self.verbose:
            print(log_msg)

    def discover_channels(self,
                          entry_point_url: str,
                          start_date: Optional[datetime.datetime] = None,
                          end_date: Optional[datetime.datetime] = None
                          ) -> List[str]:
        """
        STAGE 1: Discovers all RSS/Atom feed URLs ("channels") from a homepage.

        It fetches the homepage, parses its HTML, and looks for
        <link rel="alternate"> tags matching known feed types.

        **MODIFICATION:** This function now also detects if the entry_point_url
        is *already* an RSS/Atom feed. If so, it returns it directly.

        :param entry_point_url: The homepage URL (e.g., https://example.com)
                                 OR a direct feed URL (e.g., https://example.com/feed.xml)
        :param start_date: (Optional) Ignored by this discoverer.
        :param end_date: (Optional) Ignored by this discoverer.
        :return: A list of discovered RSS/Atom feed URLs.
        """
        self._log(f"--- STAGE 1: Discovering RSS Channels for {entry_point_url} ---")
        if start_date or end_date:
            self._log("[Info] 'start_date' and 'end_date' are ignored by RSSDiscoverer.", 1)

        self.log_messages.clear()
        found_feeds_set: Set[str] = set()

        # 1. Fetch the content
        content_bytes = self.fetcher.get_content(entry_point_url)
        if not content_bytes:
            self._log(f"[Error] Failed to fetch content: {entry_point_url}", 1)
            return []

        try:
            content_str = content_bytes.decode('utf-8', errors='ignore')
        except Exception as e:
            self._log(f"[Error] Failed to decode content: {e}", 1)
            return []

        # 2. Check if the content is XML *before* trying to parse as HTML
        # We strip leading whitespace to check the start of the document
        content_start_check = content_str.lstrip()
        if content_start_check.startswith(('<?xml', '<rss', '<feed')):
            self._log(f"Input URL appears to be an XML feed directly.", 1)
            self._log("Skipping HTML <link> tag discovery.", 2)
            found_feeds_set.add(entry_point_url)
            self._log(f"\nStage 1 Complete: Discovered 1 (self) RSS channel.")
            return list(found_feeds_set)

        self._log(f"Content does not look like XML. Proceeding with HTML parsing...", 1)

        # 3. Parse the HTML with BeautifulSoup (only if it wasn't XML)
        try:
            self._log(f"Parsing HTML from {entry_point_url}...", 1)
            soup = BeautifulSoup(content_str, 'html.parser')  # Use content_str

            # 4. Find all <link rel="alternate"> tags
            link_tags = soup.find_all(
                'link',
                rel='alternate',
                type=lambda t: t in self.FEED_MIME_TYPES
            )

            if not link_tags:
                self._log("No <link rel='alternate'> tags found.", 1)

            # 5. Extract and resolve URLs
            for tag in link_tags:
                href = tag.get('href')
                if not href:
                    continue

                # Resolve relative URLs (e.g., "/feed.xml") to absolute URLs
                absolute_url = urljoin(entry_point_url, href)

                if absolute_url not in found_feeds_set:
                    self._log(f"Found feed URL: {absolute_url}", 2)
                    found_feeds_set.add(absolute_url)

        except Exception as e:
            self._log(f"[Error] Failed during HTML parsing: {e}", 1)
            traceback.print_exc()

        self._log(f"\nStage 1 Complete: Discovered {len(found_feeds_set)} total RSS channels.")
        return list(found_feeds_set)

    def get_articles_for_channel(self, channel_url: str) -> List[str]:
        """
        STAGE 2: Fetches and parses a single RSS/Atom feed ("channel")
        to extract all individual article URLs it contains.

        (This method was correct and did not need modification)

        :param channel_url: The URL of a single RSS/Atom feed.
        :return: A list of string URLs for individual articles.
        """
        self.log_messages.clear()
        self._log(f"--- STAGE 2: Fetching articles for RSS channel {channel_url} ---")

        article_urls: List[str] = []

        # 1. Fetch the raw XML content
        xml_content_bytes = self.fetcher.get_content(channel_url)
        if not xml_content_bytes:
            self._log("[Error] Failed to fetch feed content.", 1)
            return []

        try:
            # feedparser prefers a string
            xml_content_str = xml_content_bytes.decode('utf-8', errors='ignore')
        except Exception as e:
            self._log(f"[Error] Failed to decode XML content: {e}", 1)
            return []

        # 2. Parse the feed using the utility function
        self._log("Parsing feed content with feedparser...", 1)
        feed_data = parse_feed(xml_content_str)

        if not feed_data:
            self._log(f"[Error] Failed to parse feed, 'feed_data' is None.", 1)
            return []

        # 检查 feedparser 的标准错误标志
        if hasattr(feed_data, 'bozo') and feed_data.bozo:
            self._log(f"[Warning] Feed is ill-formed (bozo=1). Errors: {feed_data.bozo_exception}", 1)
            # 即使格式不佳，也经常可以继续

        if not hasattr(feed_data, 'entries'):
            self._log(f"[Error] Parsed feed data has no 'entries' attribute.", 1)
            return []

        # 3. Extract the links from each entry
        for entry in feed_data.entries:
            if hasattr(entry, 'link') and entry.link and isinstance(entry.link, str):
                article_urls.append(entry.link)
            else:
                entry_title = entry.title if hasattr(entry, 'title') else 'N/A'
                self._log(f"[Warning] Found entry without a valid link: '{entry_title}'", 2)

        self._log(f"  > Found {len(article_urls)} articles in this channel.")
        return article_urls
