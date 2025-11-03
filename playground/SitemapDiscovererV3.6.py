#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sitemap Analyzer (PyQt5) - v3.6 (v1 Stealth Fix)

A PyQt5 application to browse and analyze a website's sitemap "channels"
and their corresponding article URLs.

VERSION 3.6 UPDATES:
- Fixed a TypeError when using the playwright-stealth v1.x fallback.
- The v1 logic now correctly instantiates 'Stealth()' before calling 'run(page)'.

Required libraries:
    pip install PyQt5 PyQtWebEngine requests ultimate-sitemap-parser playwright

    *** NEW: Install the stealth library ***
    pip install playwright-stealth

    *** IMPORTANT ***
    After installing playwright, you MUST run this in your terminal:
    python -m playwright install
    ***
"""

import sys
import requests
import xml.etree.ElementTree as ET
from usp.tree import sitemap_from_str
from urllib.parse import urlparse, urljoin
import re
from typing import Set, List, Dict, Any, Optional, Deque
from collections import deque
import traceback
from abc import ABC, abstractmethod
import datetime
try:
    from dateutil.parser import parse as date_parse
except ImportError:
    print("!!! IMPORT ERROR: 'python-dateutil' not found.")
    print("!!! Please install it for date filtering: pip install python-dateutil")
    date_parse = None

# --- Playwright Imports (with detailed error checking) ---
try:
    from playwright.sync_api import sync_playwright, Error as PlaywrightError
except ImportError:
    print("!!! IMPORT ERROR: Could not import 'playwright.sync_api'.")
    print("!!! Please ensure playwright is installed correctly: pip install playwright")
    sync_playwright = None
    PlaywrightError = None
except Exception as e:
    print(f"!!! UNEXPECTED ERROR importing playwright: {e}")
    sync_playwright = None
    PlaywrightError = None

# --- NEW: Smart Import for playwright-stealth (v1 and v2) ---
sync_stealth = None  # For v2.x
Stealth = None  # For v1.x

try:
    # Try importing v2.x style
    from playwright_stealth import sync_stealth

    print("Imported playwright-stealth v2.x ('sync_stealth') successfully.")
except ImportError:
    print("!!! Could not import 'sync_stealth' (v2.x). Trying v1.x fallback...")
    try:
        # Try importing v1.x style
        from playwright_stealth.stealth import Stealth

        print("Imported playwright-stealth v1.x ('Stealth') successfully.")
    except ImportError:
        print("!!! IMPORT ERROR: Could not import 'playwright_stealth' v1 or v2.")
        print("!!! Please ensure it is installed: pip install playwright-stealth")
    except Exception as e:
        print(f"!!! UNEXPECTED ERROR importing playwright_stealth: {e}")
except Exception as e:
    print(f"!!! UNEXPECTED ERROR importing playwright_stealth: {e}")

# Generic check to print the user-friendly message
if not sync_playwright or (not sync_stealth and not Stealth):  # Check both
    print("\n--- Library Setup Incomplete ---")
    print("One or more required Playwright libraries failed to import.")
    print("Please check the '!!! IMPORT ERROR' messages above.")
    print("To install/reinstall, run:")
    print("  pip install playwright playwright-stealth")
    print("Then install browser binaries:")
    print("  python -m playwright install")
    print("----------------------------------\n")
    if 'sync_playwright' not in locals(): sync_playwright = None
    if 'PlaywrightError' not in locals(): PlaywrightError = None

# --- PyQt5 Imports ---
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QSplitter,
    QTextEdit, QStatusBar, QTabWidget, QLabel, QFrame, QComboBox,
    QDateEdit, QCheckBox  # <-- Add QDateEdit and QCheckBox
)
from PyQt5.QtCore import (
    Qt, QRunnable, QThreadPool, QObject, pyqtSignal, QTimer,
    QDate
)
from PyQt5.QtGui import QFont, QIcon

# --- PyQtWebEngine Imports ---
try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    from PyQt5.QtCore import QUrl
except ImportError:
    print("Error: PyQtWebEngine not found.")
    print("Please install it: pip install PyQtWebEngine")
    QWebEngineView = None
    QUrl = None


# =============================================================================
#
# SECTION 1A: Fetcher Strategy Definition (Unchanged)
#
# =============================================================================

class Fetcher(ABC):
    """
    Abstract Base Class for a content fetcher.
    Defines the interface for different fetching strategies.
    """

    @abstractmethod
    def get_content(self, url: str) -> Optional[bytes]:
        """Fetches content from a URL and returns it as bytes."""
        pass

    @abstractmethod
    def close(self):
        """Cleans up any persistent resources (like sessions or browsers)."""
        pass


def also_print(log_callback):
    def wrapper(text):
        if log_callback != print:
            print(text)
        log_callback(text)
    return wrapper


class RequestsFetcher(Fetcher):
    """
    Fast, simple fetcher using requests.Session.
    Good for simple sites, but easily blocked.
    """
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
    }

    def __init__(self, log_callback=print):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self._log = also_print(log_callback)
        self._log("Using RequestsFetcher (Fast, Simple)")

    def get_content(self, url: str) -> Optional[bytes]:
        try:
            parsed_url = urlparse(url)
            referer = f"{parsed_url.scheme}://{parsed_url.netloc}/"

            response = self.session.get(
                url,
                timeout=10,
                headers={'Referer': referer}
            )
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException as e:
            self._log(f"[Request Error] Failed to fetch {url}: {e}")
            return None

    def close(self):
        self._log("Closing RequestsFetcher session.")
        self.session.close()

class PlaywrightFetcher(Fetcher):
    """
    A robust, slower fetcher that uses a real browser (Playwright)
    to bypass anti-bot measures.

    It can be configured to run in two main modes:
      1. 'Advanced' (stealth=False): Applies only a basic 'webdriver' patch.
      2. 'Stealth' (stealth=True): Applies the full 'playwright-stealth'
         patch suite to appear more human.
    """

    def __init__(self,
                 log_callback=print,
                 stealth: bool = False,
                 pause_browser: bool = False,
                 render_page: bool = True):
        """
        Initializes the Playwright browser instance.

        Args:
            log_callback:
                A callable (like print) to receive log messages.
            stealth:
                If True, apply full 'playwright-stealth' patches.
                If False, apply only the basic 'webdriver' patch.
            pause_browser:
                If True, launches in 'headful' mode (not headless) and
                calls `page.pause()` after navigation for debugging.
            render_page:
                If True, returns the final rendered HTML (`page.content()`).
                If False, returns the raw network response (`response.body()`).
                **Set to False to correctly download raw XML/JSON files.**
        """
        self._log = also_print(log_callback)
        self.stealth_mode = stealth
        self.pause_browser = pause_browser
        self.render_page = render_page

        # --- 1. Verify Library Availability ---
        if not sync_playwright:
            raise ImportError("Playwright is not installed.")
        if self.stealth_mode and (not sync_stealth and not Stealth):
            raise ImportError("Playwright-Stealth (v1 or v2) is not installed.")

        # --- 2. Start Playwright and Launch Browser ---
        try:
            mode = "Stealth" if self.stealth_mode else "Advanced"
            self._log(f"Starting PlaywrightFetcher ({mode}, Slow)...")

            # Browser is "headful" (not headless) only if debugging
            headless_mode = not self.pause_browser

            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(
                headless=headless_mode
            )

            log_msg = "Headless browser started." if headless_mode \
                      else "Headful browser started (pause_browser=True)."
            self._log(log_msg)

        except Exception as e:
            self._log(f"Failed to start Playwright: {e}")
            self._log("Please ensure you have run 'python -m playwright install'")
            raise

    def get_content(self, url: str) -> Optional[bytes]:
        """
        Fetches content from a URL using the configured Playwright instance.
        """
        context = None  # Define context in outer scope for 'finally'
        try:
            # --- 1. Create Browser Context and Page ---
            context = self.browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
            )
            page = context.new_page()

            # --- 2. Apply Browser Patches (Stealth or Basic) ---
            if self.stealth_mode:
                if sync_stealth:
                    # Use v2.x method
                    self._log("Applying full stealth patches (v2 'sync_stealth')...")
                    # TODO: Doubt about this code
                    sync_stealth(page)
                elif Stealth:
                    # Use v1.x method
                    self._log("Applying full stealth patches (v1 'Stealth.apply_stealth_sync()')...")
                    stealth_instance = Stealth()
                    stealth_instance.apply_stealth_sync(page)
                else:
                    self._log("Stealth mode selected but no library found. Applying basic patch.")
                    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            else:
                # "Advanced" mode: Apply only the basic 'webdriver' patch
                self._log("Applying basic 'webdriver' patch...")
                page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            # --- 3. Navigate to the Page ---
            self._log(f"Navigating to {url}...")
            response = page.goto(url, timeout=20000, wait_until='domcontentloaded')

            # --- 4. Pause for Debugging (if enabled) ---
            if self.pause_browser:
                self._log("Browser is paused for debugging. Press 'Resume' in the Playwright inspector to continue.")
                page.pause()

            # --- 5. Validate the Response ---
            if not response or not response.ok:
                status = response.status if response else 'N/A'
                self._log(f"[Playwright Error] Failed to get valid response. Status: {status}")
                context.close()
                return None

            # --- 6. Get Content (Raw or Rendered) ---
            content_bytes: Optional[bytes]
            if self.render_page:
                # Use page.content() to get the final, rendered HTML.
                # This is what you see in "View Source" *after* JS has run.
                # WARNING: This will get the browser's "XML Viewer" HTML,
                # NOT the raw XML file itself.
                self._log("Retrieving rendered page content (page.content())...")
                content_str = page.content()
                content_bytes = content_str.encode('utf-8')
            else:
                # Use response.body() to get the raw, unmodified network response.
                # This is the *correct* way to get non-HTML content like
                # XML sitemaps, JSON, or images.
                self._log("Retrieving raw network response (response.body())...")
                content_bytes = response.body()

            # --- 7. Clean Up and Return ---
            context.close()
            return content_bytes

        except PlaywrightError as e:
            # Handle Playwright-specific errors (e.g., timeouts)
            print(traceback.format_exc())
            self._log(f"[Playwright Error] Failed to fetch {url}: {e}")
            if context:
                context.close()
            return None
        except Exception as e:
            # Handle other unexpected errors
            print(traceback.format_exc())
            self._log(f"[General Error] Playwright failed: {e}")
            if context:
                context.close()
            return None

    def close(self):
        """
        Shuts down the Playwright browser and stops the process.
        """
        self._log("Closing PlaywrightFetcher browser...")
        if hasattr(self, 'browser') and self.browser:
            self.browser.close()
        if hasattr(self, 'playwright') and self.playwright:
            self.playwright.stop()
        self._log("PlaywrightFetcher closed.")


# =============================================================================
#
# SECTION 1B: SitemapDiscoverer Class (Refactored)
# (This section is unchanged)
#
# =============================================================================

class SitemapDiscoverer:
    """
    v3: Decoupled from request logic.
    Requires a 'Fetcher' instance to be injected upon initialization.
    All network I/O is delegated to self.fetcher.

    v3.7 (Refactor): Now includes date filtering to avoid processing
    stale sitemap indexes.
    """
    NAMESPACES = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

    def __init__(self, fetcher: Fetcher, verbose: bool = True):
        """
        Initializes the discoverer with a specific fetcher strategy.
        :param fetcher: An instance of a class that implements the Fetcher ABC.
        :param verbose: Whether to print detailed log messages.
        """
        self.verbose = verbose
        self.fetcher = fetcher  # Injected dependency

        # --- State Properties ---
        self.all_article_urls: Set[str] = set()
        self.leaf_sitemaps: Set[str] = set()
        self.to_process_queue: Deque[str] = deque()
        self.processed_sitemaps: Set[str] = set()
        self.log_messages: List[str] = []  # For GUI logging

        # --- NEW: Check for dateutil library ---
        if not date_parse:
            self._log("[Warning] 'python-dateutil' not found. Date filtering will be disabled.")

    def _log(self, message: str, indent: int = 0):
        """Unified logging function."""
        log_msg = f"{' ' * (indent * 4)}{message}"
        self.log_messages.append(log_msg)
        if self.verbose:
            print(log_msg)

    def _get_content(self, url: str) -> Optional[bytes]:
        """
        Delegates the fetching to the injected fetcher object.
        """
        # All network logic is now encapsulated in the fetcher
        return self.fetcher.get_content(url)

    # ... (The rest of the SitemapDiscoverer class is identical to v2) ...
    # ... (_discover_sitemap_entry_points & _parse_sitemap_xml) ...

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
        robots_content_bytes = self._get_content(robots_url)

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

    # --- NEW: Date parsing and checking helper ---
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

    # --- UPDATED: _parse_sitemap_xml now returns richer data ---
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

    # --- UPDATED: discover_channels now accepts dates and filters ---
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
            xml_content = self._get_content(sitemap_url)

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
        xml_content = self._get_content(channel_url)
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
        content = self._get_content(url)
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


# =============================================================================
#
# SECTION 2: PyQt5 Threading Workers (QRunnable)
# (UPDATED: Now understands 'Stealth' strategy)
#
# =============================================================================

class WorkerSignals(QObject):
    """Defines the signals available from a running worker thread."""
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(object)
    progress = pyqtSignal(str)  # For sending log messages

class ChannelDiscoveryWorker(QRunnable):
    """Worker thread for Stage 1: Discovering all channels."""

    def __init__(self,
                 strategy_name: str,
                 homepage_url: str,
                 start_date: datetime.datetime,
                 end_date: datetime.datetime,
                 pause_browser: bool,
                 render_page: bool):
        super(ChannelDiscoveryWorker, self).__init__()
        self.strategy_name = strategy_name
        self.homepage_url = homepage_url
        self.start_date = start_date
        self.end_date = end_date
        self.pause_browser = pause_browser
        self.render_page = render_page
        self.signals = WorkerSignals()

    def run(self):
        fetcher: Optional[Fetcher] = None
        try:
            log_callback = self.signals.progress.emit

            # 1. Create Fetcher *inside the worker thread*
            if "Stealth (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright not installed.")
                if not sync_stealth and not Stealth: raise ImportError("Playwright-Stealth not installed.")
                fetcher = PlaywrightFetcher(
                    log_callback=log_callback,
                    stealth=True,
                    pause_browser=self.pause_browser,
                    render_page=self.render_page
                )
            elif "Advanced (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright not installed.")
                fetcher = PlaywrightFetcher(
                    log_callback=log_callback,
                    stealth=False,
                    pause_browser=self.pause_browser,
                    render_page=self.render_page
                )
            else:  # "Simple (Requests)"
                fetcher = RequestsFetcher(log_callback=log_callback)

            # 2. Create Discoverer, injecting the new fetcher
            discoverer = SitemapDiscoverer(fetcher, verbose=True)

            # 3. Do the work (passing in the dates)
            channel_list = discoverer.discover_channels(
                self.homepage_url,
                start_date=self.start_date,
                end_date=self.end_date
            )
            self.signals.result.emit(channel_list)

        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e), traceback.format_exc()))  # Send traceback
        finally:
            # 4. Clean up the fetcher *on this thread*
            if fetcher:
                fetcher.close()
            self.signals.finished.emit()


class ArticleListWorker(QRunnable):
    """Worker thread for Stage 2 (Lazy Loading): Gets articles for one channel."""

    def __init__(self,
                 strategy_name: str,
                 channel_url: str,
                 pause_browser: bool,
                 render_page: bool):
        super(ArticleListWorker, self).__init__()
        self.strategy_name = strategy_name
        self.channel_url = channel_url
        self.pause_browser = pause_browser
        self.render_page = render_page
        self.signals = WorkerSignals()

    def run(self):
        fetcher: Optional[Fetcher] = None
        try:
            log_callback = self.signals.progress.emit

            # 1. Create Fetcher
            if "Stealth (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright not installed.")
                if not sync_stealth and not Stealth: raise ImportError("Playwright-Stealth not installed.")
                fetcher = PlaywrightFetcher(
                    log_callback=log_callback,
                    stealth=True,
                    pause_browser=self.pause_browser,
                    render_page=self.render_page
                )
            elif "Advanced (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright not installed.")
                fetcher = PlaywrightFetcher(
                    log_callback=log_callback,
                    stealth=False,
                    pause_browser=self.pause_browser,
                    render_page=self.render_page
                )
            else:  # "Simple (Requests)"
                fetcher = RequestsFetcher(log_callback=log_callback)

            # 2. Create Discoverer
            discoverer = SitemapDiscoverer(fetcher, verbose=True)

            # 3. Do the work
            # Note: The 'render_page' option will affect XML parsing.
            # The original code hardcoded 'render_page=False' here,
            # but this change respects the user's checkbox selection.
            article_list = discoverer.get_articles_for_channel(self.channel_url)
            self.signals.result.emit({
                'channel_url': self.channel_url,
                'articles': article_list
            })
        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e), traceback.format_exc()))  # Send traceback
        finally:
            # 4. Clean up
            if fetcher:
                fetcher.close()
            self.signals.finished.emit()  # Need finished signal here too


class XmlContentWorker(QRunnable):
    """Worker thread to fetch raw XML content for the text viewer."""

    def __init__(self,
                 strategy_name: str,
                 url: str,
                 pause_browser: bool,
                 render_page: bool):
        super(XmlContentWorker, self).__init__()
        self.strategy_name = strategy_name
        self.url = url
        self.pause_browser = pause_browser
        self.render_page = render_page
        self.signals = WorkerSignals()

    def run(self):
        fetcher: Optional[Fetcher] = None
        try:
            log_callback = self.signals.progress.emit

            # 1. Create Fetcher
            if "Stealth (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright not installed.")
                if not sync_stealth and not Stealth: raise ImportError("Playwright-Stealth not installed.")
                fetcher = PlaywrightFetcher(
                    log_callback=log_callback,
                    stealth=True,
                    pause_browser=self.pause_browser,
                    render_page=self.render_page  # Pass user's choice
                )
            elif "Advanced (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright not installed.")
                fetcher = PlaywrightFetcher(
                    log_callback=log_callback,
                    stealth=False,
                    pause_browser=self.pause_browser,
                    render_page=self.render_page  # Pass user's choice
                )
            else:  # "Simple (Requests)"
                fetcher = RequestsFetcher(log_callback=log_callback)

            # 2. Create Discoverer
            discoverer = SitemapDiscoverer(fetcher, verbose=True)

            # 3. Do the work
            xml_string = discoverer.get_xml_content_str(self.url)
            self.signals.result.emit(xml_string)
        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e), traceback.format_exc()))  # Send traceback
        finally:
            # 4. Clean up
            if fetcher:
                fetcher.close()
            self.signals.finished.emit()


# =============================================================================
#
# SECTION 3: PyQt5 Main Application (GUI Updated)
#
# =============================================================================

class SitemapAnalyzerApp(QMainWindow):
    """Main application window for the Sitemap Analyzer."""

    def __init__(self):
        super().__init__()

        # --- Internal State ---
        # GUI no longer holds fetcher/discoverer. Only the strategy name.
        self.fetcher_strategy_name: str = "Simple (Requests)"
        self.pause_browser: bool = False  # <-- NEW: Store fetcher option
        self.render_page: bool = False  # <-- NEW: Store fetcher option

        self.thread_pool = QThreadPool()
        # Limit thread count to avoid overwhelming the system
        self.thread_pool.setMaxThreadCount(QThreadPool.globalInstance().maxThreadCount() // 2 + 1)

        self.channel_item_map: Dict[str, QTreeWidgetItem] = {}
        self.log_history_view: Optional[QTextEdit] = None  # <-- NEW: Reference for log widget

        # --- Initialize UI ---
        self.init_ui()
        self.setWindowTitle("Sitemap Channel Analyzer (v3.7 - UI Update)")  # Version bump
        self.setWindowIcon(QIcon.fromTheme("internet-web-browser"))
        self.setGeometry(100, 100, 1200, 800)

    def init_ui(self):
        """Set up the main UI layout."""

        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # --- 1. Top URL Input Bar ---
        top_bar_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter website homepage URL (e.g., https://www.example.com)")
        self.url_input.returnPressed.connect(self.start_channel_discovery)
        top_bar_layout.addWidget(self.url_input, 1)  # Give URL input stretch factor

        # --- REQ 1: Date Period Selectors ---
        top_bar_layout.addWidget(QLabel("From:"))
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setDate(QDate.currentDate().addDays(-7))
        self.start_date_edit.setCalendarPopup(True)
        top_bar_layout.addWidget(self.start_date_edit)

        top_bar_layout.addWidget(QLabel("To:"))
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setDate(QDate.currentDate())
        self.end_date_edit.setCalendarPopup(True)
        top_bar_layout.addWidget(self.end_date_edit)

        # --- Strategy Selector Dropdown (Original) ---
        strategy_label = QLabel("Strategy:")
        top_bar_layout.addWidget(strategy_label)

        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems([
            "Simple (Requests)",
            "Advanced (Playwright)",
            "Stealth (Playwright)"
        ])
        if not sync_playwright:
            self.strategy_combo.model().item(1).setEnabled(False)
            self.strategy_combo.model().item(2).setEnabled(False)
            self.strategy_combo.setToolTip("Playwright not found. Please install it.")
        if not sync_stealth and not Stealth:  # Check both
            self.strategy_combo.model().item(2).setEnabled(False)
            self.strategy_combo.setToolTip("Playwright-Stealth not found. Please run 'pip install playwright-stealth'")

        self.strategy_combo.setCurrentIndex(0)  # Default to Simple
        top_bar_layout.addWidget(self.strategy_combo)

        # --- REQ 2: Fetcher Option Checkboxes ---
        self.pause_browser_check = QCheckBox("Pause Browser")
        self.pause_browser_check.setToolTip("Pauses Playwright (in headful mode) for debugging.")
        top_bar_layout.addWidget(self.pause_browser_check)

        self.render_page_check = QCheckBox("Render Page")
        self.render_page_check.setToolTip(
            "Fetches final rendered HTML (slower) instead of raw network response (faster).\n"
            "Warning: May break XML parsing if checked.")
        top_bar_layout.addWidget(self.render_page_check)

        # --- Analyze Button (Original) ---
        self.analyze_button = QPushButton("Analyze")
        self.analyze_button.clicked.connect(self.start_channel_discovery)
        top_bar_layout.addWidget(self.analyze_button)

        main_layout.addLayout(top_bar_layout)

        # --- REQ 3 & 4: Resizable Panes ---
        # Create a top-to-bottom splitter
        vertical_splitter = QSplitter(Qt.Vertical)

        # --- 2. Main Content Splitter (Tree | Tabs) ---
        # This is the original splitter, now it goes in the TOP pane
        self.main_splitter = QSplitter(Qt.Horizontal)

        # --- 2a. Left Side: Tree Widget ---
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabels(["Channels / Articles"])
        self.tree_widget.itemClicked.connect(self.on_tree_item_clicked)
        self.tree_widget.itemChanged.connect(self.on_tree_item_changed)
        self.main_splitter.addWidget(self.tree_widget)

        # --- 2b. Right Side: Tab Widget (Preview | Source) ---
        self.tab_widget = QTabWidget()

        if QWebEngineView:
            self.web_view = QWebEngineView()
            self.tab_widget.addTab(self.web_view, "Article Preview")
        else:
            self.web_view = QTextEdit("QWebEngineView not available. Install PyQtWebEngine.")
            self.web_view.setReadOnly(True)
            self.tab_widget.addTab(self.web_view, "Article Preview (Unavailable)")

        self.xml_viewer = QTextEdit()
        self.xml_viewer.setReadOnly(True)
        self.xml_viewer.setFont(QFont("Courier", 10))
        self.xml_viewer.setLineWrapMode(QTextEdit.NoWrap)
        self.tab_widget.addTab(self.xml_viewer, "Sitemap XML Source")

        self.main_splitter.addWidget(self.tab_widget)
        self.main_splitter.setSizes([350, 850])

        # Add the main content splitter to the TOP pane
        vertical_splitter.addWidget(self.main_splitter)

        # --- 3. Bottom: Resizable (Code | Log) Splitter ---
        # Create a new LEFT-to-RIGHT splitter for the BOTTOM pane
        bottom_splitter = QSplitter(Qt.Horizontal)

        # --- 3a. Bottom-Left: Python Filter Code (Original, but modified) ---
        filter_box = QFrame()
        filter_box.setFrameShape(QFrame.StyledPanel)
        filter_layout = QVBoxLayout(filter_box)
        filter_layout.setSpacing(5)
        filter_layout.setContentsMargins(5, 5, 5, 5)
        filter_label = QLabel("Python Filter Code (Auto-generated):")
        filter_label.setStyleSheet("font-weight: bold;")
        filter_layout.addWidget(filter_label)
        self.filter_code_text = QTextEdit()
        self.filter_code_text.setReadOnly(True)
        self.filter_code_text.setFont(QFont("Courier", 9))
        filter_layout.addWidget(self.filter_code_text)

        # Add filter box to the bottom-left pane
        bottom_splitter.addWidget(filter_box)

        # --- 3b. REQ 4: Bottom-Right: Log History ---
        log_box = QFrame()
        log_box.setFrameShape(QFrame.StyledPanel)
        log_layout = QVBoxLayout(log_box)
        log_layout.setSpacing(5)
        log_layout.setContentsMargins(5, 5, 5, 5)
        log_label = QLabel("Log History:")
        log_label.setStyleSheet("font-weight: bold;")
        log_layout.addWidget(log_label)
        self.log_history_view = QTextEdit()
        self.log_history_view.setReadOnly(True)
        self.log_history_view.setFont(QFont("Courier", 9))
        self.log_history_view.setLineWrapMode(QTextEdit.NoWrap)
        log_layout.addWidget(self.log_history_view)

        # Add log box to the bottom-right pane
        bottom_splitter.addWidget(log_box)

        # Set initial size for the bottom (Code | Log) splitter
        bottom_splitter.setSizes([600, 600])

        # Add the bottom splitter to the BOTTOM pane
        vertical_splitter.addWidget(bottom_splitter)

        # Set initial size for the main (Top | Bottom) splitter
        # This gives the bottom panel an initial height of ~150-200
        vertical_splitter.setSizes([600, 200])

        # Add the main vertical splitter (which contains everything) to the layout
        # The '1' makes it stretch to fill the window
        main_layout.addWidget(vertical_splitter, 1)

        # --- 4. Status Bar (Unchanged) ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready. Enter a URL and select a strategy.")

        self.setCentralWidget(main_widget)
        self.update_filter_code()

    def set_loading_state(self, is_loading: bool, message: str = ""):
        """Enable/Disable UI controls during threaded operations."""
        self.url_input.setEnabled(not is_loading)
        self.analyze_button.setEnabled(not is_loading)
        self.tree_widget.setEnabled(not is_loading)
        self.strategy_combo.setEnabled(not is_loading)

        # --- NEW: Disable new controls ---
        self.start_date_edit.setEnabled(not is_loading)
        self.end_date_edit.setEnabled(not is_loading)
        self.pause_browser_check.setEnabled(not is_loading)
        self.render_page_check.setEnabled(not is_loading)

        if is_loading:
            self.status_bar.showMessage(message)
            self.analyze_button.setText("Loading...")
            # Also log to history
            if self.log_history_view:
                self.log_history_view.append(f"--- {message} ---")
        else:
            self.status_bar.showMessage(message or "Ready.")
            self.analyze_button.setText("Analyze")
            if self.log_history_view and message:
                self.log_history_view.append(f"--- {message} ---")

    def clear_all_controls(self):
        """Reset the UI to its initial state."""
        self.tree_widget.clear()
        self.channel_item_map.clear()
        self.xml_viewer.clear()
        self.filter_code_text.clear()
        if self.log_history_view:
            self.log_history_view.clear()  # <-- NEW: Clear log history
        if self.web_view and QUrl:
            self.web_view.setUrl(QUrl("about:blank"))
        self.update_filter_code()

    # --- NEW: Slot for Log History ---
    def append_log_history(self, message: str):
        """Appends a message to the log history text area."""
        if self.log_history_view:
            self.log_history_view.append(message)

    # --- Threaded Action Starters ---

    def start_channel_discovery(self):
        """
        Slot for 'Analyze' button.
        Passes the *strategy name* and *options* to the worker.
        """
        url = self.url_input.text().strip()
        if not url:
            self.status_bar.showMessage("Error: Please enter a URL.")
            return

        if not url.startswith("http"):
            url = "https://" + url
            self.url_input.setText(url)

        self.clear_all_controls()

        # --- NEW: Get values from new UI elements ---
        # Get dates (as datetime objects)
        start_date = self.start_date_edit.dateTime().toPyDateTime()
        end_date_qdt = self.end_date_edit.dateTime()
        # Set end date to end-of-day
        end_date = end_date_qdt.toPyDateTime().replace(hour=23, minute=59, second=59)

        # Store the selected strategy name and options
        self.fetcher_strategy_name = self.strategy_combo.currentText()
        self.pause_browser = self.pause_browser_check.isChecked()
        self.render_page = self.render_page_check.isChecked()

        self.set_loading_state(True, f"Discovering channels for {url} using {self.fetcher_strategy_name}...")

        # Pass all options to the worker
        worker = ChannelDiscoveryWorker(
            strategy_name=self.fetcher_strategy_name,
            homepage_url=url,
            start_date=start_date,
            end_date=end_date,
            pause_browser=self.pause_browser,
            render_page=self.render_page
        )

        # Connect signals
        worker.signals.result.connect(self.on_channel_discovery_result)
        worker.signals.finished.connect(self.on_channel_discovery_finished)
        worker.signals.error.connect(self.on_worker_error)
        worker.signals.progress.connect(self.status_bar.showMessage)
        worker.signals.progress.connect(self.append_log_history)  # <-- NEW: Connect to log

        self.thread_pool.start(worker)

    def start_article_loading(self, channel_item: QTreeWidgetItem, channel_url: str):
        """
        Starts the Stage 2 (Lazy Loading) worker for a specific channel.
        """
        channel_item.takeChildren()  # Remove dummy
        loading_item = QTreeWidgetItem(["Loading articles..."])
        channel_item.addChild(loading_item)
        channel_item.setExpanded(True)
        self.status_bar.showMessage(f"Loading articles for {channel_url}...")

        # Pass the stored strategy name and options
        worker = ArticleListWorker(
            strategy_name=self.fetcher_strategy_name,
            channel_url=channel_url,
            pause_browser=self.pause_browser,
            render_page=self.render_page
        )
        worker.signals.result.connect(self.on_article_list_result)
        worker.signals.finished.connect(self.on_worker_finished)  # Use generic finished
        worker.signals.error.connect(self.on_worker_error)
        worker.signals.progress.connect(self.status_bar.showMessage)
        worker.signals.progress.connect(self.append_log_history)  # <-- NEW: Connect to log

        self.thread_pool.start(worker)

    def start_xml_content_loading(self, url: str):
        """
        Starts the worker to fetch raw XML for the viewer.
        """
        self.xml_viewer.setPlainText(f"Loading XML content from {url}...")
        self.tab_widget.setCurrentWidget(self.xml_viewer)

        # Pass the stored strategy name and options
        worker = XmlContentWorker(
            strategy_name=self.fetcher_strategy_name,
            url=url,
            pause_browser=self.pause_browser,
            render_page=self.render_page
        )
        worker.signals.result.connect(self.on_xml_content_result)
        worker.signals.finished.connect(self.on_worker_finished)  # Use generic finished
        worker.signals.error.connect(self.on_worker_error)
        worker.signals.progress.connect(self.status_bar.showMessage)
        worker.signals.progress.connect(self.append_log_history)  # <-- NEW: Connect to log

        self.thread_pool.start(worker)

    # --- Thread Result Slots ---

    def on_channel_discovery_result(self, channel_list: List[str]):
        """Slot for ChannelDiscoveryWorker 'result' signal."""
        if not channel_list:
            self.status_bar.showMessage("No sitemap channels (leaf nodes) found.")
            return

        self.tree_widget.setDisabled(True)
        # Use QTimer to avoid freezing GUI when adding many items
        self.channel_queue = deque(channel_list)
        QTimer.singleShot(0, self.add_channels_to_tree)

    def add_channels_to_tree(self):
        """Process a chunk of channels to add to the tree."""
        count = 0
        while self.channel_queue and count < 100:  # Add 100 items at a time
            channel_url = self.channel_queue.popleft()
            item = QTreeWidgetItem([channel_url])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
            item.setData(0, Qt.UserRole, {
                'type': 'channel', 'url': channel_url, 'loaded': False
            })
            item.addChild(QTreeWidgetItem())  # Add dummy child for lazy loading
            self.tree_widget.addTopLevelItem(item)
            self.channel_item_map[channel_url] = item
            count += 1

        if self.channel_queue:
            # If more items, schedule next chunk
            QTimer.singleShot(0, self.add_channels_to_tree)
        else:
            # All done
            self.tree_widget.setDisabled(False)
            self.status_bar.showMessage(f"Found {len(self.channel_item_map)} channels. Click to load articles.")

    def on_channel_discovery_finished(self):
        """Slot for *ChannelDiscoveryWorker* 'finished' signal."""
        # Only the main discovery task reenables the UI
        self.set_loading_state(False, "Discovery complete.")

    def on_worker_finished(self):
        """Generic 'finished' slot for sub-tasks (article/xml loading)."""
        # We don't want to re-enable the main UI, just show ready
        if not self.analyze_button.isEnabled():
            # Check if pool is idle before showing "Ready"
            if self.thread_pool.activeThreadCount() == 0:
                self.status_bar.showMessage("Task complete. Ready.", 3000)

    def on_article_list_result(self, result: Dict[str, Any]):
        """Slot for ArticleListWorker 'result' signal."""
        channel_url = result['channel_url']
        article_list = result['articles']
        parent_item = self.channel_item_map.get(channel_url)
        if not parent_item: return
        data = parent_item.data(0, Qt.UserRole)
        data['loaded'] = True
        parent_item.setData(0, Qt.UserRole, data)
        parent_item.takeChildren()
        if not article_list:
            parent_item.addChild(QTreeWidgetItem(["No articles found in this channel."]))
        else:
            for article_url in article_list:
                child_item = QTreeWidgetItem([article_url])
                child_item.setData(0, Qt.UserRole, {'type': 'article', 'url': article_url})
                parent_item.addChild(child_item)
        parent_item.setExpanded(True)
        self.status_bar.showMessage(f"Loaded {len(article_list)} articles for {channel_url}", 5000)

    def on_xml_content_result(self, xml_string: str):
        """Slot for XmlContentWorker 'result' signal."""
        self.xml_viewer.setPlainText(xml_string)

    def on_worker_error(self, error: tuple):
        """Slot for any worker's 'error' signal."""
        ex_type, message, tb = error
        error_msg = f"Error: {ex_type}: {message}"
        self.status_bar.showMessage(error_msg)

        # --- NEW: Log full error to history ---
        if self.log_history_view:
            self.log_history_view.append(f"--- Worker Error ---")
            self.log_history_view.append(error_msg)
            self.log_history_view.append(tb)  # Log full traceback
            self.log_history_view.append(f"--------------------")

        print(f"--- Worker Error ---")
        print(tb)  # Print the full traceback to console
        print(f"--------------------")

        # If the main discovery fails, re-enable UI. Sub-tasks won't.
        if "ChannelDiscoveryWorker" in str(ex_type) or "ChannelDiscoveryWorker" in tb:
            self.set_loading_state(False, f"Error occurred. {message}")

    # --- UI Event Handlers ---

    def on_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handles clicks on any tree item (channel or article)."""
        # Prevent clicks while UI is disabled
        if not self.tree_widget.isEnabled():
            return

        data = item.data(0, Qt.UserRole)
        if not data: return

        item_type = data.get('type')
        url = data.get('url')

        if item_type == 'channel':
            # Check if it's already loading (has one child named "Loading...")
            if item.childCount() == 1 and "Loading" in item.child(0).text(0):
                return  # Already loading, do nothing

            if data.get('loaded') == False:
                self.start_article_loading(item, channel_url=url)

            self.start_xml_content_loading(url=url)

        elif item_type == 'article':
            if self.web_view and QUrl:
                self.web_view.setUrl(QUrl(url))
                self.web_view.setFocus()
                self.tab_widget.setCurrentWidget(self.web_view)
                self.status_bar.showMessage(f"Loading page: {url}", 3000)

    def on_tree_item_changed(self, item: QTreeWidgetItem, column: int):
        """Handles checkbox state changes to update the filter code."""
        data = item.data(0, Qt.UserRole)
        if data and data.get('type') == 'channel':
            self.update_filter_code()

    def update_filter_code(self):
        """Generates the Python filter code based on checked items."""
        selected_paths = []
        for i in range(self.tree_widget.topLevelItemCount()):
            item = self.tree_widget.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                data = item.data(0, Qt.UserRole)
                if data:
                    try:
                        path = urlparse(data.get('url')).path
                        selected_paths.append(path)
                    except Exception:
                        pass
        header = "# Auto-generated Python filter...\n"
        header += "from urllib.parse import urlparse\n\n"
        if not selected_paths:
            code = "def should_process_channel(channel_url: str) -> bool:\n"
            code += "    return False # No channels selected\n"
        else:
            code = "SELECTED_CHANNEL_PATHS = {\n"
            for path in sorted(selected_paths):
                code += f"    \"{path}\",\n"
            code += "}\n\n"
            code += "def should_process_channel(channel_url: str) -> bool:\n"
            code += "    try:\n"
            code += "        path = urlparse(channel_url).path\n"
            code += "        return path in SELECTED_CHANNEL_PATHS\n"
            code += "    except Exception:\n"
            code += "        return False\n"
        self.filter_code_text.setPlainText(header + code)

    def closeEvent(self, event):
        """Ensure threads are cleaned up on exit."""
        self.status_bar.showMessage("Shutting down... waiting for tasks...")
        self.thread_pool.waitForDone(3000)  # Wait 3 secs for workers
        self.thread_pool.clear()  # Clear pending runnables
        # Fetchers are now closed by the workers themselves.
        event.accept()


# =============================================================================
#
# SECTION 4: Main Execution
#
# =============================================================================

if __name__ == "__main__":
    # Add some safety checks for required modules
    if not sync_playwright or not QWebEngineView:
        print("\n--- FATAL ERROR ---")
        if not sync_playwright:
            print("Playwright library is not installed or failed to import.")
        if not QWebEngineView:
            print("PyQtWebEngine is not installed or failed to import.")
        print("Please install required libraries and try again.")
        print("pip install PyQtWebEngine playwright playwright-stealth")
        print("python -m playwright install")

        # We can still run, but advanced features will be disabled.
        # sys.exit(-1) # Or just let it run in a degraded state

    app = QApplication(sys.argv)

    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    main_window = SitemapAnalyzerApp()
    main_window.show()

    sys.exit(app.exec_())
