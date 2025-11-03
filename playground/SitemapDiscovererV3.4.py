#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sitemap Analyzer (PyQt5) - v3.4 (playwright-stealth)

A PyQt5 application to browse and analyze a website's sitemap "channels"
and their corresponding article URLs.

VERSION 3.4 UPDATES:
- Fixed a typo in the stealth import.
- Renamed 'stealth_sync' to 'sync_stealth' to match the playwright-stealth library.

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

try:
    # --- NEW: Import playwright-stealth ---
    from playwright_stealth import sync_stealth  # <-- FIX: Renamed from stealth_sync
except ImportError as e:
    print(str(e))
    print("!!! IMPORT ERROR: Could not import 'playwright_stealth'.")
    print("!!! Please ensure it is installed: pip install playwright-stealth")
    sync_stealth = None
except Exception as e:
    print(str(e))
    print(f"!!! UNEXPECTED ERROR importing playwright_stealth: {e}")
    sync_stealth = None

# Generic check to print the user-friendly message
if not sync_playwright or not sync_stealth:  # <-- FIX: Renamed from stealth_sync
    print("\n--- Library Setup Incomplete ---")
    print("One or more required Playwright libraries failed to import.")
    print("Please check the '!!! IMPORT ERROR' messages above.")
    print("To install/reinstall, run:")
    print("  pip install playwright playwright-stealth")
    print("Then install browser binaries:")
    print("  python -m playwright install")
    print("----------------------------------\n")
    # We still set them to None so the GUI can (partially) load and disable options
    if 'sync_playwright' not in locals(): sync_playwright = None
    if 'PlaywrightError' not in locals(): PlaywrightError = None
    if 'sync_stealth' not in locals(): sync_stealth = None  # <-- FIX: Renamed from stealth_sync

# --- PyQt5 Imports ---
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QSplitter,
    QTextEdit, QStatusBar, QTabWidget, QLabel, QFrame, QComboBox
)
from PyQt5.QtCore import (
    Qt, QRunnable, QThreadPool, QObject, pyqtSignal
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


# =m===========================================================================
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
        self._log = log_callback
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
    Robust, slower fetcher using a real browser (Playwright).
    Can be run in 'advanced' (basic patch) or 'stealth' (full patches) mode.
    """

    def __init__(self, log_callback=print, stealth: bool = False):
        """
        :param log_callback: Function to send log messages to.
        :param stealth: Whether to apply the full 'playwright-stealth' patches.
        """
        self._log = log_callback
        self.stealth_mode = stealth
        if not sync_playwright or (stealth and not sync_stealth):  # <-- FIX: Renamed from stealth_sync
            raise ImportError("Playwright or Playwright-Stealth is not installed.")

        try:
            mode = "Stealth" if self.stealth_mode else "Advanced"
            self._log(f"Starting PlaywrightFetcher ({mode}, Slow)...")
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=True)
            self._log("Headless browser started.")
        except Exception as e:
            self._log(f"Failed to start Playwright: {e}")
            self._log("Please ensure you have run 'python -m playwright install'")
            raise

    def get_content(self, url: str) -> Optional[bytes]:
        try:
            context = self.browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
            )
            page = context.new_page()

            # --- UPDATED: Conditional Stealth ---
            if self.stealth_mode:
                # Apply all patches (plugins, webgl, fonts, webdriver, etc.)
                self._log("Applying full stealth patches...")
                sync_stealth(page)  # <-- FIX: Renamed from stealth_sync
            else:
                # Apply only the basic 'webdriver' patch (v3.2)
                self._log("Applying basic 'webdriver' patch...")
                page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            # --- END UPDATED ---

            # Navigate and wait for the page to be fully loaded
            self._log(f"Navigating to {url}...")
            page.goto(url, timeout=20000, wait_until='domcontentloaded')
            self._log(f"Page loaded. Getting content...")

            # Get the final content (after any JS execution/redirects)
            content_str = page.content()
            content_bytes = content_str.encode('utf-8')

            context.close()
            return content_bytes
        except PlaywrightError as e:
            self._log(f"[Playwright Error] Failed to fetch {url}: {e}")
            if 'context' in locals() and context:
                context.close()
            return None
        except Exception as e:
            self._log(f"[General Error] Playwright failed: {e}")
            if 'context' in locals() and context:
                context.close()
            return None

    def close(self):
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
# =============================================================================

class SitemapDiscoverer:
    """
    v3: Decoupled from request logic.
    Requires a 'Fetcher' instance to be injected upon initialization.
    All network I/O is delegated to self.fetcher.
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

    def _parse_sitemap_xml(self, xml_content: bytes, sitemap_url: str) -> Dict[str, List[str]]:
        """Parses Sitemap XML content with a fallback mechanism."""
        pages: List[str] = []
        sub_sitemaps: List[str] = []
        try:
            self._log("    Trying to parse with [ultimate-sitemap-parser]...", 1)
            parsed_sitemap = sitemap_from_str(xml_content.decode('utf-8', errors='ignore'))
            for page in parsed_sitemap.all_pages():
                pages.append(page.url)
            for sub_sitemap in parsed_sitemap.all_sub_sitemaps():
                sub_sitemaps.append(sub_sitemap.url)
            self._log(f"    [USP Success] Found {len(pages)} pages and {len(sub_sitemaps)} sub-sitemaps.", 1)
        except Exception as e:
            self._log(f"    [USP Failed] Library parsing error: {e}", 1)
            self._log("    --> Initiating [Manual ElementTree] fallback...", 1)
            try:
                root = ET.fromstring(xml_content)
                index_nodes = root.findall('ns:sitemap', self.NAMESPACES)
                if index_nodes:
                    for node in index_nodes:
                        loc = node.find('ns:loc', self.NAMESPACES)
                        if loc is not None and loc.text:
                            sub_sitemaps.append(loc.text)
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

    def discover_channels(self, homepage_url: str) -> List[str]:
        """STAGE 1: Discover all "channels" (leaf sitemaps containing articles)."""
        self._log(f"--- STAGE 1: Discovering Channels for {homepage_url} ---")
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
            sitemap_url = self.to_process_queue.popleft()
            if sitemap_url in self.processed_sitemaps:
                continue
            self.processed_sitemaps.add(sitemap_url)
            self._log(f"\n--- Analyzing index: {sitemap_url} ---")
            xml_content = self._get_content(sitemap_url)
            if not xml_content:
                self._log("  Failed to fetch, skipping.", 1)
                continue
            parse_result = self._parse_sitemap_xml(xml_content, sitemap_url)
            if parse_result['sub_sitemaps']:
                self._log(f"  > Found {len(parse_result['sub_sitemaps'])} sub-indexes. Adding to queue.", 2)
                self.to_process_queue.extend(parse_result['sub_sitemaps'])
            if parse_result['pages']:
                self._log(f"  > Found {len(parse_result['pages'])} pages. Marking as 'Channel'.", 2)
                self.leaf_sitemaps.add(sitemap_url)

        self._log(f"\nStage 1 Complete: Discovered {len(self.leaf_sitemaps)} total channels.")
        return list(self.leaf_sitemaps)

    def get_articles_for_channel(self, channel_url: str) -> List[str]:
        """
        Helper for Stage 2 (Lazy Loading): Gets pages for ONE specific channel.
        """
        self.log_messages.clear()
        self._log(f"--- STAGE 2: Fetching articles for {channel_url} ---")
        xml_content = self._get_content(channel_url)
        if not xml_content:
            return []

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

    def __init__(self, strategy_name: str, homepage_url: str):
        super(ChannelDiscoveryWorker, self).__init__()
        self.strategy_name = strategy_name
        self.homepage_url = homepage_url
        self.signals = WorkerSignals()

    def run(self):
        fetcher: Optional[Fetcher] = None
        try:
            log_callback = self.signals.progress.emit

            # 1. Create Fetcher *inside the worker thread*
            if "Stealth (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright/Stealth not installed.")
                fetcher = PlaywrightFetcher(log_callback=log_callback, stealth=True)
            elif "Advanced (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright not installed.")
                fetcher = PlaywrightFetcher(log_callback=log_callback, stealth=False)
            else:  # "Simple (Requests)"
                fetcher = RequestsFetcher(log_callback=log_callback)

            # 2. Create Discoverer, injecting the new fetcher
            discoverer = SitemapDiscoverer(fetcher, verbose=False)

            # 3. Do the work
            channel_list = discoverer.discover_channels(self.homepage_url)
            self.signals.result.emit(channel_list)

        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e)))
        finally:
            # 4. Clean up the fetcher *on this thread*
            if fetcher:
                fetcher.close()
            self.signals.finished.emit()


class ArticleListWorker(QRunnable):
    """Worker thread for Stage 2 (Lazy Loading): Gets articles for one channel."""

    def __init__(self, strategy_name: str, channel_url: str):
        super(ArticleListWorker, self).__init__()
        self.strategy_name = strategy_name
        self.channel_url = channel_url
        self.signals = WorkerSignals()

    def run(self):
        fetcher: Optional[Fetcher] = None
        try:
            log_callback = self.signals.progress.emit

            # 1. Create Fetcher
            if "Stealth (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright/Stealth not installed.")
                fetcher = PlaywrightFetcher(log_callback=log_callback, stealth=True)
            elif "Advanced (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright not installed.")
                fetcher = PlaywrightFetcher(log_callback=log_callback, stealth=False)
            else:  # "Simple (Requests)"
                fetcher = RequestsFetcher(log_callback=log_callback)

            # 2. Create Discoverer
            discoverer = SitemapDiscoverer(fetcher, verbose=False)

            # 3. Do the work
            article_list = discoverer.get_articles_for_channel(self.channel_url)
            self.signals.result.emit({
                'channel_url': self.channel_url,
                'articles': article_list
            })
        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e)))
        finally:
            # 4. Clean up
            if fetcher:
                fetcher.close()
            self.signals.finished.emit()  # Need finished signal here too


class XmlContentWorker(QRunnable):
    """Worker thread to fetch raw XML content for the text viewer."""

    def __init__(self, strategy_name: str, url: str):
        super(XmlContentWorker, self).__init__()
        self.strategy_name = strategy_name
        self.url = url
        self.signals = WorkerSignals()

    def run(self):
        fetcher: Optional[Fetcher] = None
        try:
            log_callback = self.signals.progress.emit

            # 1. Create Fetcher
            if "Stealth (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright/Stealth not installed.")
                fetcher = PlaywrightFetcher(log_callback=log_callback, stealth=True)
            elif "Advanced (Playwright)" in self.strategy_name:
                if not sync_playwright: raise ImportError("Playwright not installed.")
                fetcher = PlaywrightFetcher(log_callback=log_callback, stealth=False)
            else:  # "Simple (Requests)"
                fetcher = RequestsFetcher(log_callback=log_callback)

            # 2. Create Discoverer
            discoverer = SitemapDiscoverer(fetcher, verbose=False)

            # 3. Do the work
            xml_string = discoverer.get_xml_content_str(self.url)
            self.signals.result.emit(xml_string)
        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e)))
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

        self.thread_pool = QThreadPool()
        self.channel_item_map: Dict[str, QTreeWidgetItem] = {}

        # --- Initialize UI ---
        self.init_ui()
        self.setWindowTitle("Sitemap Channel Analyzer (v3.4 - Stealth Fix)")  # Version bump
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
        top_bar_layout.addWidget(self.url_input, 1)

        # --- Strategy Selector Dropdown ---
        strategy_label = QLabel("Strategy:")
        top_bar_layout.addWidget(strategy_label)

        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems([
            "Simple (Requests)",
            "Advanced (Playwright)",
            "Stealth (Playwright)"  # --- NEW: Third option ---
        ])
        if not sync_playwright:
            self.strategy_combo.model().item(1).setEnabled(False)
            self.strategy_combo.model().item(2).setEnabled(False)
            self.strategy_combo.setToolTip("Playwright not found. Please install it.")
        if not sync_stealth:  # <-- FIX: Renamed from stealth_sync
            self.strategy_combo.model().item(2).setEnabled(False)
            self.strategy_combo.setToolTip("Playwright-Stealth not found. Please run 'pip install playwright-stealth'")

        self.strategy_combo.setCurrentIndex(0)  # Default to Simple
        top_bar_layout.addWidget(self.strategy_combo)

        self.analyze_button = QPushButton("Analyze")
        self.analyze_button.clicked.connect(self.start_channel_discovery)
        top_bar_layout.addWidget(self.analyze_button)

        main_layout.addLayout(top_bar_layout)

        # --- 2. Main Content Splitter (Tree | Tabs) ---
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

        main_layout.addWidget(self.main_splitter, 1)

        # --- 3. Bottom: Python Filter Code ---
        filter_box = QFrame()
        # ... (This section is unchanged) ...
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
        self.filter_code_text.setFixedHeight(150)
        filter_layout.addWidget(self.filter_code_text)
        main_layout.addWidget(filter_box)

        # --- 4. Status Bar ---
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
        self.strategy_combo.setEnabled(not is_loading)  # Disable strategy combo

        if is_loading:
            self.status_bar.showMessage(message)
            self.analyze_button.setText("Loading...")
        else:
            self.status_bar.showMessage(message or "Ready.")
            self.analyze_button.setText("Analyze")

    def clear_all_controls(self):
        """Reset the UI to its initial state."""
        self.tree_widget.clear()
        self.channel_item_map.clear()
        self.xml_viewer.clear()
        self.filter_code_text.clear()
        if self.web_view and QUrl:
            self.web_view.setUrl(QUrl("about:blank"))
        self.update_filter_code()

    # --- Threaded Action Starters ---

    def start_channel_discovery(self):
        """
        Slot for 'Analyze' button.
        Passes the *strategy name* to the worker.
        """
        url = self.url_input.text().strip()
        if not url:
            self.status_bar.showMessage("Error: Please enter a URL.")
            return

        if not url.startswith("http"):
            url = "https://" + url
            self.url_input.setText(url)

        self.clear_all_controls()

        # Store the selected strategy name
        self.fetcher_strategy_name = self.strategy_combo.currentText()

        self.set_loading_state(True, f"Discovering channels for {url} using {self.fetcher_strategy_name}...")

        # Pass the strategy *name*, not an instance
        worker = ChannelDiscoveryWorker(self.fetcher_strategy_name, url)

        # Connect signals
        worker.signals.result.connect(self.on_channel_discovery_result)
        worker.signals.finished.connect(self.on_channel_discovery_finished)
        worker.signals.error.connect(self.on_worker_error)
        worker.signals.progress.connect(self.status_bar.showMessage)  # Connect progress

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

        # Pass the stored strategy name
        worker = ArticleListWorker(self.fetcher_strategy_name, channel_url)
        worker.signals.result.connect(self.on_article_list_result)
        worker.signals.finished.connect(self.on_worker_finished)  # Use generic finished
        worker.signals.error.connect(self.on_worker_error)
        worker.signals.progress.connect(self.status_bar.showMessage)

        self.thread_pool.start(worker)

    def start_xml_content_loading(self, url: str):
        """
        Starts the worker to fetch raw XML for the viewer.
        """
        self.xml_viewer.setPlainText(f"Loading XML content from {url}...")
        self.tab_widget.setCurrentWidget(self.xml_viewer)

        # Pass the stored strategy name
        worker = XmlContentWorker(self.strategy_name, url)
        worker.signals.result.connect(self.on_xml_content_result)
        worker.signals.finished.connect(self.on_worker_finished)  # Use generic finished
        worker.signals.error.connect(self.on_worker_error)
        worker.signals.progress.connect(self.status_bar.showMessage)

        self.thread_pool.start(worker)

    # --- Thread Result Slots ---

    def on_channel_discovery_result(self, channel_list: List[str]):
        """Slot for ChannelDiscoveryWorker 'result' signal."""
        if not channel_list:
            self.status_bar.showMessage("No sitemap channels (leaf nodes) found.")
            return

        self.tree_widget.setDisabled(True)
        for channel_url in channel_list:
            item = QTreeWidgetItem([channel_url])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
            item.setData(0, Qt.UserRole, {
                'type': 'channel', 'url': channel_url, 'loaded': False
            })
            item.addChild(QTreeWidgetItem())
            self.tree_widget.addTopLevelItem(item)
            self.channel_item_map[channel_url] = item
        self.tree_widget.setDisabled(False)
        self.status_bar.showMessage(f"Found {len(channel_list)} channels. Click to load articles.")

    def on_channel_discovery_finished(self):
        """Slot for *ChannelDiscoveryWorker* 'finished' signal."""
        # Only the main discovery task reenables the UI
        self.set_loading_state(False, "Discovery complete.")

    def on_worker_finished(self):
        """Generic 'finished' slot for sub-tasks (article/xml loading)."""
        # We don't want to re-enable the main UI, just show ready
        if not self.analyze_button.isEnabled():
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
        ex_type, message = error
        self.status_bar.showMessage(f"Error: {ex_type}: {message}")
        print(f"Worker Error: {ex_type}: {message}")
        traceback.print_exc()
        # If the main discovery fails, re-enable UI. Sub-tasks won't.
        if "ChannelDiscoveryWorker" in str(ex_type):
            self.set_loading_state(False, f"Error occurred. {message}")

    # --- UI Event Handlers ---
    # (This section is unchanged)

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
        self.thread_pool.clear()  # Clear pending runnables
        # Fetchers are now closed by the workers themselves.
        event.accept()


# =============================================================================
#
# SECTION 4: Main Execution
#
# =============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)

    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    main_window = SitemapAnalyzerApp()
    main_window.show()

    sys.exit(app.exec_())

