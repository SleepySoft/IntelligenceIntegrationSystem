#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Crawler Playground (v4.0)
A GUI application for discovering, fetching, and extracting web content
using various strategies and libraries.
"""

import sys
import datetime
import traceback
from collections import deque
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional

# --- Core Component Imports ---
# Assuming a file structure like:
# IntelligenceCrawler/
#   Discoverer.py (contains IDiscoverer, SitemapDiscoverer, RSSDiscoverer)
#   Fetcher.py (contains Fetcher, RequestsFetcher, PlaywrightFetcher)
#   Extractor.py (contains IExtractor, TrafilaturaExtractor, etc.)

try:
    from IntelligenceCrawler.Fetcher import Fetcher, PlaywrightFetcher, RequestsFetcher
except ImportError:
    print("!!! CRITICAL: Could not import Fetcher classes.")


    # Mock classes to allow UI to load
    class Fetcher:
        pass


    class PlaywrightFetcher:
        pass


    class RequestsFetcher:
        pass

try:
    from IntelligenceCrawler.Discoverer import IDiscoverer, SitemapDiscoverer, RSSDiscoverer
except ImportError:
    print("!!! CRITICAL: Could not import Discoverer classes.")


    class IDiscoverer:
        pass


    class SitemapDiscoverer:
        pass


    class RSSDiscoverer:
        pass

try:
    from IntelligenceCrawler.Extractor import (
        IExtractor, TrafilaturaExtractor, ReadabilityExtractor,
        Newspaper3kExtractor, GenericCSSExtractor, Crawl4AIExtractor, ExtractionResult
)

    # Store imported classes for factory
    EXTRACTOR_MAP = {
        "Trafilatura": TrafilaturaExtractor,
        "Readability": ReadabilityExtractor,
        "Newspaper3k": Newspaper3kExtractor,
        "Generic CSS": GenericCSSExtractor,
        "Crawl4AI": Crawl4AIExtractor,
    }
except ImportError:
    print("!!! CRITICAL: Could not import Extractor classes.")
    EXTRACTOR_MAP = {}


    class IExtractor:
        pass

try:
    from dateutil.parser import parse as date_parse
except ImportError:
    print("!!! IMPORT ERROR: 'python-dateutil' not found.")
    date_parse = None

# --- Playwright Imports (with detailed error checking) ---
try:
    from playwright.sync_api import sync_playwright, Error as PlaywrightError
except ImportError:
    print("!!! IMPORT ERROR: Could not import 'playwright.sync_api'.")
    sync_playwright = None
    PlaywrightError = None
except Exception as e:
    sync_playwright = None
    PlaywrightError = None

# --- Smart Import for playwright-stealth (v1 and v2) ---
sync_stealth = None  # For v2.x
Stealth = None  # For v1.x
try:
    from playwright_stealth import sync_stealth

    print("Imported playwright-stealth v2.x ('sync_stealth') successfully.")
except ImportError:
    try:
        from playwright_stealth.stealth import Stealth

        print("Imported playwright-stealth v1.x ('Stealth') successfully.")
    except ImportError:
        print("!!! IMPORT ERROR: Could not import 'playwright_stealth' v1 or v2.")
    except Exception:
        pass
except Exception:
    pass

# --- PyQt5 Imports ---
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QSplitter,
    QTextEdit, QStatusBar, QTabWidget, QLabel, QFrame, QComboBox,
    QDateEdit, QCheckBox, QToolBar, QSizePolicy, QSpinBox,
    QMenu, QAction
)
from PyQt5.QtCore import (
    Qt, QRunnable, QThreadPool, QObject, pyqtSignal, QTimer, QSettings
)
from PyQt5.QtGui import QFont, QIcon

# --- PyQtWebEngine Imports ---
try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    from PyQt5.QtCore import QUrl
except ImportError:
    print("Error: PyQtWebEngine not found. Web preview will be disabled.")
    QWebEngineView = None
    QUrl = None


# =============================================================================
#
# SECTION 1: Utility Factories
# (To create instances inside workers)
#
# =============================================================================
def create_fetcher_instance(fetcher_name: str,
                            log_callback,
                            proxy: Optional[str] = None,
                            timeout: int = 10,  # <-- NEW (in seconds)
                            **kwargs) -> Fetcher:
    """
    Factory to create a fetcher instance based on its name.
    (工厂函数：根据名称创建 fetcher 实例。)
    """
    stealth_mode = "Stealth" in fetcher_name
    pause = kwargs.get('pause_browser', False)
    render = kwargs.get('render_page', False)

    # We assume the Fetcher classes have been modified to accept 'timeout'
    # in their __init__ and apply it appropriately (e.g., to self.timeout).
    # (我们假设 Fetcher 类已被修改以在 __init__ 中接受 'timeout'。)

    if "Playwright" in fetcher_name:
        if not sync_playwright: raise ImportError("Playwright not installed.")
        if stealth_mode and (not sync_stealth and not Stealth):
            raise ImportError("Playwright-Stealth not installed.")

        return PlaywrightFetcher(
            log_callback=log_callback,
            proxy=proxy,
            timeout_s=timeout,  # <-- NEW (pass ms)
            stealth=stealth_mode,
            pause_browser=pause,
            render_page=render
        )
    else:  # "Simple (Requests)"
        return RequestsFetcher(
            log_callback=log_callback,
            proxy=proxy,
            timeout_s=timeout
        )


def create_discoverer_instance(discoverer_name: str, fetcher: Fetcher, log_callback) -> IDiscoverer:
    """Factory to create a discoverer instance based on its name."""
    if discoverer_name == "Sitemap":
        if 'SitemapDiscoverer' not in globals(): raise ImportError("SitemapDiscoverer not found.")
        return SitemapDiscoverer(fetcher, verbose=True)
    elif discoverer_name == "RSS":
        if 'RSSDiscoverer' not in globals(): raise ImportError("RSSDiscoverer not found.")
        return RSSDiscoverer(fetcher, verbose=True)
    # elif discoverer_name == "Smart Analysis":
    #     return SmartDiscoverer(fetcher, verbose=True) # Future
    else:
        raise ValueError(f"Unknown discoverer_name: {discoverer_name}")


def create_extractor_instance(extractor_name: str, log_callback) -> IExtractor:
    """Factory to create an extractor instance based on its name."""
    if extractor_name not in EXTRACTOR_MAP:
        raise ImportError(f"Extractor '{extractor_name}' not found or failed to import.")

    ExtractorClass = EXTRACTOR_MAP[extractor_name]
    return ExtractorClass(verbose=True)


# =============================================================================
#
# SECTION 2: PyQt5 Threading Workers (QRunnable)
# (Refactored to be generic)
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

    # REFACTORED: Now accepts names instead of hardcoding logic
    def __init__(self,
                 discoverer_name: str,
                 fetcher_name: str,
                 homepage_url: str,
                 start_date: datetime.datetime,
                 end_date: datetime.datetime,
                 proxy: Optional[str],
                 timeout: int,
                 pause_browser: bool,
                 render_page: bool):
        super(ChannelDiscoveryWorker, self).__init__()
        self.discoverer_name = discoverer_name
        self.fetcher_name = fetcher_name
        self.homepage_url = homepage_url
        self.start_date = start_date
        self.end_date = end_date
        self.proxy = proxy
        self.timeout = timeout
        self.pause_browser = pause_browser
        self.render_page = render_page  # Note: This is for XML, may break parsing
        self.signals = WorkerSignals()

    def run(self):
        fetcher: Optional[Fetcher] = None
        try:
            log_callback = self.signals.progress.emit

            # 1. Create Fetcher
            # Note: Forcing render_page=False for discovery, as it's
            # almost always parsing XML/Text, not rendered HTML.
            if self.render_page:
                log_callback("[Warning] 'Render Page' is enabled for Discovery, " \
                             "this may fail XML/RSS parsing. Forcing False.")

            fetcher = create_fetcher_instance(
                self.fetcher_name,
                log_callback,
                proxy=self.proxy,
                timeout=self.timeout,
                pause_browser=self.pause_browser,
                render_page=False  # Force False for discovery
            )

            # 2. Create Discoverer
            discoverer = create_discoverer_instance(self.discoverer_name, fetcher, log_callback)

            # 3. Do the work
            channel_list = discoverer.discover_channels(
                self.homepage_url,
                start_date=self.start_date,
                end_date=self.end_date
            )
            self.signals.result.emit(channel_list)

        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e), traceback.format_exc()))
        finally:
            if fetcher: fetcher.close()
            self.signals.finished.emit()


class ArticleListWorker(QRunnable):
    """Worker thread for Stage 2 (Lazy Loading): Gets articles for one channel."""

    # REFACTORED: Now accepts names
    def __init__(self,
                 discoverer_name: str,
                 fetcher_name: str,
                 channel_url: str,
                 proxy: Optional[str],
                 timeout: int,
                 pause_browser: bool,
                 render_page: bool):
        super(ArticleListWorker, self).__init__()
        self.discoverer_name = discoverer_name
        self.fetcher_name = fetcher_name
        self.channel_url = channel_url
        self.proxy = proxy
        self.timeout = timeout
        self.pause_browser = pause_browser
        self.render_page = render_page
        self.signals = WorkerSignals()

    def run(self):
        fetcher: Optional[Fetcher] = None
        try:
            log_callback = self.signals.progress.emit

            # 1. Create Fetcher
            if self.render_page:
                log_callback("[Warning] 'Render Page' is enabled for Article List, " \
                             "this may fail XML/RSS parsing. Forcing False.")

            fetcher = create_fetcher_instance(
                self.fetcher_name,
                log_callback,
                proxy=self.proxy,
                timeout=self.timeout,
                pause_browser=self.pause_browser,
                render_page=False  # Force False for discovery
            )

            # 2. Create Discoverer
            discoverer = create_discoverer_instance(self.discoverer_name, fetcher, log_callback)

            # 3. Do the work
            article_list = discoverer.get_articles_for_channel(self.channel_url)
            self.signals.result.emit({
                'channel_url': self.channel_url,
                'articles': article_list
            })
        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e), traceback.format_exc()))
        finally:
            if fetcher: fetcher.close()
            self.signals.finished.emit()


class ChannelSourceWorker(QRunnable):
    """
    Worker thread to fetch raw channel content (e.g., XML) for the text viewer.
    (REFACTORED from XmlContentWorker)
    """

    def __init__(self,
                 discoverer_name: str,  # Discoverer needed for get_content_str
                 fetcher_name: str,
                 url: str,
                 proxy: Optional[str],
                 timeout: int,
                 pause_browser: bool,
                 render_page: bool):
        super(ChannelSourceWorker, self).__init__()
        self.discoverer_name = discoverer_name
        self.fetcher_name = fetcher_name
        self.url = url
        self.proxy = proxy
        self.timeout = timeout
        self.pause_browser = pause_browser
        self.render_page = render_page
        self.signals = WorkerSignals()

    def run(self):
        fetcher: Optional[Fetcher] = None
        try:
            log_callback = self.signals.progress.emit

            # 1. Create Fetcher
            if self.render_page:
                log_callback("[Warning] 'Render Page' is enabled for Channel Source, "
                             "this may fail XML/RSS parsing. Forcing False.")

            fetcher = create_fetcher_instance(
                self.fetcher_name,
                log_callback,
                proxy=self.proxy,
                timeout=self.timeout,
                pause_browser=self.pause_browser,
                render_page=False  # Force False for discovery
            )

            # 2. Create Discoverer (only for its .get_content_str method)
            discoverer = create_discoverer_instance(self.discoverer_name, fetcher, log_callback)

            # 3. Do the work (using the generic interface method)
            content_string = discoverer.get_content_str(self.url)
            self.signals.result.emit(content_string)
        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e), traceback.format_exc()))
        finally:
            if fetcher: fetcher.close()
            self.signals.finished.emit()


# --- NEW WORKER FOR EXTRACTION (REQ 2e) ---
class ExtractionWorker(QRunnable):
    """Worker thread for Stage 3: Fetching and Extracting a single article."""

    def __init__(self,
                 fetcher_name: str,
                 extractor_name: str,
                 url_to_extract: str,
                 extractor_kwargs: dict,
                 proxy: Optional[str],
                 timeout: int,
                 pause_browser: bool,
                 render_page: bool):
        super(ExtractionWorker, self).__init__()
        self.fetcher_name = fetcher_name
        self.extractor_name = extractor_name
        self.url_to_extract = url_to_extract
        self.extractor_kwargs = extractor_kwargs
        self.proxy = proxy
        self.timeout = timeout
        self.pause_browser = pause_browser
        self.render_page = render_page  # This SHOULD be respected
        self.signals = WorkerSignals()

    def run(self):
        fetcher: Optional[Fetcher] = None
        try:
            log_callback = self.signals.progress.emit

            # 1. Create Fetcher
            log_callback(f"Fetching {self.url_to_extract} using {self.fetcher_name}...")
            fetcher = create_fetcher_instance(
                self.fetcher_name,
                log_callback,
                proxy=self.proxy,
                timeout=self.timeout,
                pause_browser=self.pause_browser,
                render_page=self.render_page
            )

            # 2. Get Content
            content_bytes = fetcher.get_content(self.url_to_extract)
            if not content_bytes:
                raise ValueError("Failed to fetch content (returned None).")

            log_callback(f"Fetched {len(content_bytes)} bytes. Extracting using {self.extractor_name}...")

            # 3. Create Extractor
            extractor = create_extractor_instance(self.extractor_name, log_callback)

            # 4. Do the work
            markdown_result = extractor.extract(
                content_bytes,
                self.url_to_extract,
                **self.extractor_kwargs
            )
            self.signals.result.emit(markdown_result)

        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e), traceback.format_exc()))
        finally:
            if fetcher: fetcher.close()
            self.signals.finished.emit()


# =============================================================================
#
# SECTION 3: PyQt5 Main Application (GUI Refactored)
#
# =============================================================================

# --- REQ 4: New Name ---
class CrawlerPlaygroundApp(QMainWindow):
    """
    Main application window for the Crawler Playground.
    Provides a UI to test Discoverer, Fetcher, and Extractor combinations.
    """

    def __init__(self):
        super().__init__()

        # --- Internal State ---
        self.discoverer_name: str = "Sitemap"
        self.discovery_fetcher_name: str = "Simple (Requests)"
        self.pause_browser: bool = False
        self.render_page: bool = False

        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(QThreadPool.globalInstance().maxThreadCount() // 2 + 1)

        self.channel_item_map: Dict[str, QTreeWidgetItem] = {}
        self.log_history_view: Optional[QTextEdit] = None

        # --- NEW: Settings for URL History ---
        self.URL_HISTORY_KEY = "discovery_url_history"
        self.MAX_URL_HISTORY = 25

        # --- Initialize UI ---
        self.init_ui()
        self._load_url_history()
        self.connect_signals()  # Centralize signal connections
        self.setWindowTitle("Crawler Playground (v4.0)")
        self.setWindowIcon(QIcon.fromTheme("internet-web-browser"))
        self.setGeometry(100, 100, 1400, 900)
        self.update_generated_code()  # Show initial code

    def init_ui(self):
        """Set up the main UI layout."""

        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # --- 1. Top URL Input Bar (Refactored) ---
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setSpacing(10)

        # --- MODIFICATION: Replace QLineEdit with QComboBox ---
        self.url_input = QComboBox()
        self.url_input.setEditable(True)
        self.url_input.setPlaceholderText("Enter website homepage URL (e.g., https://www.example.com)")
        self.url_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Connect 'Enter' key press in the editable line edit
        self.url_input.lineEdit().returnPressed.connect(self.start_channel_discovery)

        # --- NEW: Add Context Menu for clearing history ---
        self.url_input.setContextMenuPolicy(Qt.CustomContextMenu)
        self.url_input.customContextMenuRequested.connect(self._show_url_history_context_menu)

        top_bar_layout.addWidget(self.url_input, 1)  # Give it stretch

        # --- REQ 1: Discoverer Dropdown ---
        top_bar_layout.addWidget(QLabel("Discoverer:"))
        self.discoverer_combo = QComboBox()
        self.discoverer_combo.addItems(["Sitemap", "RSS", "Smart Analysis (WIP)"])
        if "RSSDiscoverer" not in globals():
            self.discoverer_combo.model().item(1).setEnabled(False)
        self.discoverer_combo.model().item(2).setEnabled(False)  # WIP
        # --- MODIFICATION: Add ToolTip to explain behavior ---
        self.discoverer_combo.setToolTip(
            "Select the discovery method:\n"
            "- Sitemap: Finds sitemap.xml from the homepage.\n"
            "- RSS: Finds <link rel='alternate'> RSS feeds from the homepage.\n\n"
            "In both cases, enter the homepage URL."
        )
        top_bar_layout.addWidget(self.discoverer_combo)

        # --- NEW: Date Period Refactor (Request 1) ---
        self.date_filter_check = QCheckBox("Filter last:")
        self.date_filter_check.setToolTip("If checked, only discover channels/articles updated within the last X days.")
        top_bar_layout.addWidget(self.date_filter_check)

        self.date_filter_days_spin = QSpinBox()
        self.date_filter_days_spin.setRange(1, 9999)
        self.date_filter_days_spin.setValue(7)
        self.date_filter_days_spin.setSuffix(" days")
        self.date_filter_days_spin.setEnabled(False)  # Disabled by default
        top_bar_layout.addWidget(self.date_filter_days_spin)

        # Connect checkbox to enable/disable the spinbox
        self.date_filter_check.stateChanged.connect(
            lambda state: self.date_filter_days_spin.setEnabled(state == Qt.Checked)
        )

        # --- Discovery Fetcher Strategy (Original) ---
        top_bar_layout.addWidget(QLabel("Fetcher:"))
        self.discovery_fetcher_combo = QComboBox()
        self.discovery_fetcher_combo.addItems([
            "Simple (Requests)",
            "Advanced (Playwright)",
            "Stealth (Playwright)"
        ])
        if not sync_playwright:
            self.discovery_fetcher_combo.model().item(1).setEnabled(False)
            self.discovery_fetcher_combo.model().item(2).setEnabled(False)
        if not sync_stealth and not Stealth:
            self.discovery_fetcher_combo.model().item(2).setEnabled(False)
        top_bar_layout.addWidget(self.discovery_fetcher_combo)

        # --- Fetcher Option Checkboxes (Original) ---
        self.pause_browser_check = QCheckBox("Pause Browser")
        self.pause_browser_check.setToolTip("Pauses Playwright (in headful mode) for debugging.")
        top_bar_layout.addWidget(self.pause_browser_check)

        self.render_page_check = QCheckBox("Render Page")
        self.render_page_check.setToolTip(
            "Fetches final rendered HTML (slower).\n"
            "[Discovery] Will be forced OFF to ensure XML/RSS parsing.\n"
            "[Extraction] Will be used as set.")
        top_bar_layout.addWidget(self.render_page_check)

        top_bar_layout.addSpacing(5)  # Add small space
        top_bar_layout.addWidget(QLabel("Timeout(s):"))
        self.discovery_timeout_spin = QSpinBox()
        self.discovery_timeout_spin.setRange(1, 300)  # 1s to 5min
        self.discovery_timeout_spin.setValue(10)  # Default 10
        self.discovery_timeout_spin.setToolTip("Fetcher timeout in seconds for discovery.")
        top_bar_layout.addWidget(self.discovery_timeout_spin)

        top_bar_layout.addSpacing(15)  # Add larger space

        # --- NEW: Discovery Proxy Input ---

        top_bar_layout.addWidget(QLabel("Proxy:"))
        self.discovery_proxy_input = QLineEdit()
        self.discovery_proxy_input.setPlaceholderText("e.g., http://user:pass@host:port")
        self.discovery_proxy_input.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
        top_bar_layout.addWidget(self.discovery_proxy_input)

        # --- MODIFICATION: Use addStretch with a factor ---
        # The url_input has stretch 1, this will take up remaining space
        top_bar_layout.addStretch(1)

        self.analyze_button = QPushButton("Discover Channels")  # Renamed
        self.analyze_button.setStyleSheet("padding: 5px 10px;")  # Add padding
        top_bar_layout.addWidget(self.analyze_button)

        main_layout.addLayout(top_bar_layout)

        # --- Top-to-Bottom splitter ---
        vertical_splitter = QSplitter(Qt.Vertical)

        # --- 2. Main Content Splitter (Tree | Tabs) ---
        self.main_splitter = QSplitter(Qt.Horizontal)

        # --- 2a. Left Side: Tree Widget ---
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabels(["Discovered Channels / Articles"])
        self.main_splitter.addWidget(self.tree_widget)

        # --- 2b. Right Side: Tab Widget (Refactored) ---
        self.tab_widget = QTabWidget()

        # --- REQ 2 & 3: New Article Preview Tab ---
        self.article_preview_widget = self._create_article_preview_tab()
        if QWebEngineView:
            self.tab_widget.addTab(self.article_preview_widget, "Article Preview")
        else:
            self.tab_widget.addTab(QTextEdit("QWebEngineView not available."), "Preview (Disabled)")

        self.channel_source_viewer = QTextEdit()
        self.channel_source_viewer.setReadOnly(True)
        self.channel_source_viewer.setFont(QFont("Courier", 10))
        self.channel_source_viewer.setLineWrapMode(QTextEdit.NoWrap)
        self.tab_widget.addTab(self.channel_source_viewer, "Channel Source")  # Renamed

        self.main_splitter.addWidget(self.tab_widget)
        self.main_splitter.setSizes([400, 1000])
        vertical_splitter.addWidget(self.main_splitter)

        # --- 3. Bottom: (Code | Log) Splitter ---
        bottom_splitter = QSplitter(Qt.Horizontal)

        # --- 3a. Bottom-Left: Generated Code (REQ 5) ---
        code_box = QFrame()
        code_box.setFrameShape(QFrame.StyledPanel)
        code_layout = QVBoxLayout(code_box)
        code_label = QLabel("Generated Python Code:")  # Renamed
        code_label.setStyleSheet("font-weight: bold;")
        code_layout.addWidget(code_label)
        self.generated_code_text = QTextEdit()  # Renamed
        self.generated_code_text.setReadOnly(True)
        self.generated_code_text.setFont(QFont("Courier", 9))
        code_layout.addWidget(self.generated_code_text)
        bottom_splitter.addWidget(code_box)

        # --- 3b. Bottom-Right: Log History ---
        log_box = QFrame()
        log_box.setFrameShape(QFrame.StyledPanel)
        log_layout = QVBoxLayout(log_box)
        log_label = QLabel("Log History:")
        log_label.setStyleSheet("font-weight: bold;")
        log_layout.addWidget(log_label)
        self.log_history_view = QTextEdit()
        self.log_history_view.setReadOnly(True)
        self.log_history_view.setFont(QFont("Courier", 9))
        self.log_history_view.setLineWrapMode(QTextEdit.NoWrap)
        log_layout.addWidget(self.log_history_view)
        bottom_splitter.addWidget(log_box)

        bottom_splitter.setSizes([600, 600])
        vertical_splitter.addWidget(bottom_splitter)
        vertical_splitter.setSizes([700, 200])
        main_layout.addWidget(vertical_splitter, 1)

        # --- 4. Status Bar ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready. Enter a URL and select a discoverer.")

        self.setCentralWidget(main_widget)

    def _create_article_preview_tab(self) -> QWidget:
        """Helper function to build the complex Article Preview tab."""
        # This 'main_widget' is what the tab.addTab() receives.
        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        layout.setSpacing(5)
        layout.setContentsMargins(0, 5, 0, 0)  # Keep top margin

        # --- Create the main horizontal splitter ---
        self.article_splitter = QSplitter(Qt.Horizontal)
        self.article_splitter.setOpaqueResize(False)  # FIX for webview flicker

        # --- Build the Left Pane (URL Bar + Web View) ---
        left_pane_widget = QWidget()
        left_layout = QVBoxLayout(left_pane_widget)
        left_layout.setSpacing(5)
        left_layout.setContentsMargins(0, 0, 5, 0)  # Right margin

        left_toolbar = QToolBar("Article URL")
        left_toolbar.addWidget(QLabel("URL:"))
        self.article_url_input = QLineEdit()
        self.article_url_input.setPlaceholderText("Select an article from the tree...")
        left_toolbar.addWidget(self.article_url_input)
        self.article_go_button = QPushButton("Go")
        left_toolbar.addWidget(self.article_go_button)

        left_layout.addWidget(left_toolbar)  # Add toolbar to left pane

        if QWebEngineView:
            self.web_view = QWebEngineView()
        else:
            self.web_view = QTextEdit("QWebEngineView not available. Install PyQtWebEngine.")
            self.web_view.setReadOnly(True)

        left_layout.addWidget(self.web_view, 1)  # Add webview (stretches)

        # --- Build the Right Pane (Tools + Markdown View) ---
        right_pane_widget = QWidget()
        right_layout = QVBoxLayout(right_pane_widget)
        right_layout.setSpacing(5)
        right_layout.setContentsMargins(5, 0, 0, 0)  # Left margin

        # --- MODIFICATION: Create TWO toolbars ---

        # --- Toolbar 1: Fetcher Settings ---
        fetcher_toolbar = QToolBar("Fetcher Tools")
        fetcher_toolbar.layout().setSpacing(5)  # <-- MODIFICATION: Add spacing
        fetcher_toolbar.addWidget(QLabel("Fetcher:"))
        self.article_fetcher_combo = QComboBox()
        self.article_fetcher_combo.addItems([
            "Simple (Requests)",
            "Advanced (Playwright)",
            "Stealth (Playwright)"
        ])
        if not sync_playwright:
            self.article_fetcher_combo.model().item(1).setEnabled(False)
            self.article_fetcher_combo.model().item(2).setEnabled(False)
        if not sync_stealth and not Stealth:
            self.article_fetcher_combo.model().item(2).setEnabled(False)
        fetcher_toolbar.addWidget(self.article_fetcher_combo)

        fetcher_toolbar.addWidget(QLabel("Proxy:"))
        self.article_proxy_input = QLineEdit()
        self.article_proxy_input.setPlaceholderText("e.g., socks5://user:pass@host:port")
        self.article_proxy_input.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
        fetcher_toolbar.addWidget(self.article_proxy_input)

        self.article_pause_check = QCheckBox("Pause")
        self.article_pause_check.setToolTip("Pauses Playwright (in headful mode) for debugging.")
        fetcher_toolbar.addWidget(self.article_pause_check)

        self.article_render_check = QCheckBox("Render")
        self.article_render_check.setToolTip("Fetches final rendered HTML (slower) vs. raw response (faster).")
        self.article_render_check.setChecked(True)  # Default to checked
        fetcher_toolbar.addWidget(self.article_render_check)

        # --- NEW: Extraction Timeout ---
        fetcher_toolbar.addWidget(QLabel("Timeout(s):"))
        self.article_timeout_spin = QSpinBox()
        self.article_timeout_spin.setRange(1, 300)
        self.article_timeout_spin.setValue(20)  # Default 20
        self.article_timeout_spin.setToolTip("Fetcher timeout in seconds for extraction.")
        fetcher_toolbar.addWidget(self.article_timeout_spin)

        # Add a spacer to push all fetcher controls to the left
        fetcher_spacer = QWidget()
        fetcher_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        fetcher_toolbar.addWidget(fetcher_spacer)

        # --- Toolbar 2: Extractor Settings ---
        # --- Toolbar 2: Extractor Settings ---
        extractor_toolbar = QToolBar("Extractor Tools")
        extractor_toolbar.layout().setSpacing(5)  # <-- MODIFICATION: Add spacing
        extractor_toolbar.addWidget(QLabel("Extractor:"))
        self.extractor_combo = QComboBox()
        available_extractors = sorted(EXTRACTOR_MAP.keys())
        if available_extractors:
            self.extractor_combo.addItems(available_extractors)
        else:
            self.extractor_combo.addItem("No Extractors Found")
            self.extractor_combo.setEnabled(False)
        extractor_toolbar.addWidget(self.extractor_combo)

        self.extractor_settings_button = QPushButton("Settings")
        self.extractor_settings_button.setEnabled(False)  # TODO: Implement settings dialog
        extractor_toolbar.addWidget(self.extractor_settings_button)

        self.extractor_analyze_button = QPushButton("Analyze")
        extractor_toolbar.addWidget(self.extractor_analyze_button)

        # Add a spacer to push all extractor controls to the left
        extractor_spacer = QWidget()
        extractor_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        extractor_toolbar.addWidget(extractor_spacer)

        # # --- Add both toolbars to the right layout ---
        # right_layout.addWidget(fetcher_toolbar)
        # right_layout.addWidget(extractor_toolbar)
        #
        # # --- Add Markdown view ---
        # self.markdown_output_view = QTextEdit()
        # self.markdown_output_view.setReadOnly(True)
        # self.markdown_output_view.setFont(QFont("Courier", 10))
        # self.markdown_output_view.setLineWrapMode(QTextEdit.NoWrap)
        #
        # right_layout.addWidget(self.markdown_output_view, 1)  # Add markdown view (stretches)

        # --- Add both toolbars to the right layout ---
        right_layout.addWidget(fetcher_toolbar)
        right_layout.addWidget(extractor_toolbar)

        # --- NEW: Vertical Splitter for Markdown and Metadata ---
        self.output_splitter = QSplitter(Qt.Vertical)

        # --- Markdown view (Top) ---
        self.markdown_output_view = QTextEdit()
        self.markdown_output_view.setReadOnly(True)
        self.markdown_output_view.setFont(QFont("Courier", 10))
        self.markdown_output_view.setLineWrapMode(QTextEdit.NoWrap)
        self.markdown_output_view.setPlaceholderText("Extracted Markdown content will appear here...")
        self.output_splitter.addWidget(self.markdown_output_view)

        # --- Metadata view (Bottom) ---
        self.metadata_output_view = QTextEdit()  # <-- NEW WIDGET
        self.metadata_output_view.setReadOnly(True)
        self.metadata_output_view.setFont(QFont("Courier", 10))
        self.metadata_output_view.setLineWrapMode(QTextEdit.NoWrap)
        self.metadata_output_view.setPlaceholderText("Extracted metadata (JSON) will appear here...")
        self.output_splitter.addWidget(self.metadata_output_view)

        # Set initial sizes for the new splitter
        self.output_splitter.setSizes([700, 300])  # 70% Markdown, 30% Meta

        right_layout.addWidget(self.output_splitter, 1)  # Add splitter (stretches)

        # --- Add panes to splitter ---
        self.article_splitter.addWidget(left_pane_widget)
        self.article_splitter.addWidget(right_pane_widget)
        self.article_splitter.setSizes([800, 500])  # Adjust initial sizes

        layout.addWidget(self.article_splitter, 1)  # Add splitter to main layout
        return main_widget

    def connect_signals(self):
        """Centralize all signal/slot connections."""
        # Top Bar
        self.url_input.lineEdit().returnPressed.connect(self.start_channel_discovery)
        self.analyze_button.clicked.connect(self.start_channel_discovery)

        # Tree
        self.tree_widget.itemClicked.connect(self.on_tree_item_clicked)

        # Article Preview Tab
        self.article_go_button.clicked.connect(self.on_article_go_clicked)
        self.article_url_input.returnPressed.connect(self.on_article_go_clicked)
        self.extractor_analyze_button.clicked.connect(self.start_extraction_analysis)

        # Code Generation Triggers
        self.discoverer_combo.currentTextChanged.connect(self.update_generated_code)
        self.discovery_fetcher_combo.currentTextChanged.connect(self.update_generated_code)
        self.article_fetcher_combo.currentTextChanged.connect(self.update_generated_code)
        self.extractor_combo.currentTextChanged.connect(self.update_generated_code)
        self.tree_widget.itemChanged.connect(self.update_generated_code_from_tree)

    def set_loading_state(self, is_loading: bool, message: str = ""):
        """Enable/Disable UI controls during threaded operations."""
        # Top bar
        self.url_input.setEnabled(not is_loading)
        self.analyze_button.setEnabled(not is_loading)
        self.discoverer_combo.setEnabled(not is_loading)
        self.discovery_fetcher_combo.setEnabled(not is_loading)
        self.pause_browser_check.setEnabled(not is_loading)
        self.render_page_check.setEnabled(not is_loading)

        # Tree
        self.tree_widget.setEnabled(not is_loading)

        # Article Tab (partially)
        self.extractor_analyze_button.setEnabled(not is_loading)

        if is_loading:
            self.status_bar.showMessage(message)
            # Find the button that was pressed
            if "Discover" in message:
                self.analyze_button.setText("Discovering...")
            elif "Extracting" in message:
                self.extractor_analyze_button.setText("Analyzing...")

            if self.log_history_view:
                self.log_history_view.append(f"--- {message} ---")
        else:
            self.status_bar.showMessage(message or "Ready.")
            self.analyze_button.setText("Discover Channels")
            self.extractor_analyze_button.setText("Analyze")
            if self.log_history_view and message:
                self.log_history_view.append(f"--- {message} ---")

    def clear_all_controls(self):
        """Reset the UI to its initial state."""
        self.tree_widget.clear()
        self.channel_item_map.clear()
        self.channel_source_viewer.clear()
        self.generated_code_text.clear()
        # --- MODIFICATION: Clear only text, not history list ---
        self.url_input.setCurrentIndex(-1)
        self.url_input.clearEditText()
        if self.log_history_view:
            self.log_history_view.clear()
        if self.web_view and QUrl:
            self.web_view.setUrl(QUrl("about:blank"))
        self.article_url_input.clear()
        self.markdown_output_view.clear()
        self.metadata_output_view.clear()
        self.update_generated_code()

    def append_log_history(self, message: str):
        """Appends a message to the log history text area."""
        if self.log_history_view:
            self.log_history_view.append(message)

    # --- Threaded Action Starters ---

    def start_channel_discovery(self):
        """Slot for 'Discover Channels' button."""
        url = self.url_input.currentText().strip()
        if not url:
            self.status_bar.showMessage("Error: Please enter a URL.")
            return

        if not url.startswith("http"):
            url = "https://" + url
            self.url_input.setText(url)

        self.clear_all_controls()
        self._save_url_history(url)

        # --- Get values from new date controls ---
        start_date: Optional[datetime.datetime] = None
        end_date: Optional[datetime.datetime] = None

        if self.date_filter_check.isChecked():
            days_ago = self.date_filter_days_spin.value()
            end_date = datetime.datetime.now()
            start_date = end_date - datetime.timedelta(days=days_ago)
            # Log the filter being used
            self.append_log_history(f"Applying date filter: Last {days_ago} days "
                                    f"(since {start_date.strftime('%Y-%m-%d')})")

        # Store the selected strategy names and options
        self.discoverer_name = self.discoverer_combo.currentText()
        self.discovery_fetcher_name = self.discovery_fetcher_combo.currentText()
        self.pause_browser = self.pause_browser_check.isChecked()
        self.render_page = self.render_page_check.isChecked()

        self.set_loading_state(True, f"Discovering {self.discoverer_name} channels for {url}...")
        self.update_generated_code()  # Update code snippet

        proxy_str = self.discovery_proxy_input.text().strip() or None
        timeout_sec = self.discovery_timeout_spin.value()

        worker = ChannelDiscoveryWorker(
            discoverer_name=self.discoverer_name,
            fetcher_name=self.discovery_fetcher_name,
            homepage_url=url,
            start_date=start_date,
            end_date=end_date,
            proxy=proxy_str,
            timeout=timeout_sec,
            pause_browser=self.pause_browser,
            render_page=self.render_page
        )

        worker.signals.result.connect(self.on_channel_discovery_result)
        worker.signals.finished.connect(self.on_channel_discovery_finished)
        worker.signals.error.connect(self.on_worker_error)
        worker.signals.progress.connect(self.status_bar.showMessage)
        worker.signals.progress.connect(self.append_log_history)

        self.thread_pool.start(worker)

    def start_article_loading(self, channel_item: QTreeWidgetItem, channel_url: str):
        """Starts the Stage 2 (Lazy Loading) worker for a specific channel."""
        channel_item.takeChildren()  # Remove dummy
        loading_item = QTreeWidgetItem(["Loading articles..."])
        channel_item.addChild(loading_item)
        channel_item.setExpanded(True)
        self.status_bar.showMessage(f"Loading articles for {channel_url}...")

        proxy_str = self.discovery_proxy_input.text().strip() or None
        timeout_sec = self.discovery_timeout_spin.value()

        worker = ArticleListWorker(
            discoverer_name=self.discoverer_name,
            fetcher_name=self.discovery_fetcher_name,
            channel_url=channel_url,
            proxy=proxy_str,
            timeout=timeout_sec,
            pause_browser=self.pause_browser,
            render_page=self.render_page
        )

        worker.signals.result.connect(self.on_article_list_result)
        worker.signals.finished.connect(self.on_worker_finished)
        worker.signals.error.connect(self.on_worker_error)
        worker.signals.progress.connect(self.status_bar.showMessage)
        worker.signals.progress.connect(self.append_log_history)

        self.thread_pool.start(worker)

    def start_channel_source_loading(self, url: str):
        """Starts worker to fetch raw channel source (e.g., XML) for the viewer."""
        self.channel_source_viewer.setPlainText(f"Loading source from {url}...")
        self.tab_widget.setCurrentWidget(self.channel_source_viewer)

        proxy_str = self.discovery_proxy_input.text().strip() or None
        timeout_sec = self.discovery_timeout_spin.value()

        worker = ChannelSourceWorker(
            discoverer_name=self.discoverer_name,
            fetcher_name=self.discovery_fetcher_name,
            url=url,
            proxy=proxy_str,
            timeout=timeout_sec,
            pause_browser=self.pause_browser,
            render_page=self.render_page
        )

        worker.signals.result.connect(self.on_channel_source_result)
        worker.signals.finished.connect(self.on_worker_finished)
        worker.signals.error.connect(self.on_worker_error)
        worker.signals.progress.connect(self.status_bar.showMessage)
        worker.signals.progress.connect(self.append_log_history)

        self.thread_pool.start(worker)

    def start_extraction_analysis(self):
        """Slot for the 'Analyze' button in the Article Preview tab."""
        url = self.article_url_input.text().strip()
        if not url:
            self.status_bar.showMessage("Error: No article URL to analyze.")
            return

        fetcher_name = self.article_fetcher_combo.currentText()
        extractor_name = self.extractor_combo.currentText()

        # TODO: Get kwargs from a dialog opened by self.extractor_settings_button
        extractor_kwargs = {}
        if extractor_name == "Generic CSS":
            # This is where you would pop a dialog to ask for selectors
            # For now, we'll hardcode a placeholder
            self.append_log_history("[Warning] Generic CSS Extractor running with no selectors.")
            extractor_kwargs = {'selectors': ['body'], 'exclude_selectors': ['nav', 'footer']}

        self.markdown_output_view.setPlainText(f"Starting analysis on {url}...")
        self.metadata_output_view.setPlainText("Waiting for analysis to complete...")  # <-- NEW
        self.set_loading_state(True, f"Extracting {url} with {extractor_name}...")
        self.update_generated_code()  # Update code snippet

        proxy_str = self.article_proxy_input.text().strip() or None
        timeout_sec = self.article_timeout_spin.value()

        worker = ExtractionWorker(
            fetcher_name=fetcher_name,
            extractor_name=extractor_name,
            url_to_extract=url,
            extractor_kwargs=extractor_kwargs,
            proxy=proxy_str,
            timeout=timeout_sec,
            pause_browser=self.article_pause_check.isChecked(),
            render_page=self.article_render_check.isChecked()
        )

        worker.signals.result.connect(self.on_extraction_result)
        worker.signals.finished.connect(self.on_extraction_finished)
        worker.signals.error.connect(self.on_worker_error)
        worker.signals.progress.connect(self.status_bar.showMessage)
        worker.signals.progress.connect(self.append_log_history)

        self.thread_pool.start(worker)

    # --- Thread Result Slots ---

    def on_channel_discovery_result(self, channel_list: List[str]):
        """Slot for ChannelDiscoveryWorker 'result' signal."""
        if not channel_list:
            self.status_bar.showMessage("No channels found.")
            return

        self.tree_widget.setDisabled(True)
        self.channel_queue = deque(channel_list)
        QTimer.singleShot(0, self.add_channels_to_tree)

    def add_channels_to_tree(self):
        """Process a chunk of channels to add to the tree."""
        count = 0
        while self.channel_queue and count < 100:
            channel_url = self.channel_queue.popleft()
            item = QTreeWidgetItem([channel_url])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
            item.setData(0, Qt.UserRole, {
                'type': 'channel', 'url': channel_url, 'loaded': False
            })
            item.addChild(QTreeWidgetItem())  # Dummy child for lazy loading
            self.tree_widget.addTopLevelItem(item)
            self.channel_item_map[channel_url] = item
            count += 1

        if self.channel_queue:
            QTimer.singleShot(0, self.add_channels_to_tree)
        else:
            self.tree_widget.setDisabled(False)
            self.status_bar.showMessage(f"Found {len(self.channel_item_map)} channels. Click to load articles.")
            self.update_generated_code()  # Update code now that tree is populated

    def on_channel_discovery_finished(self):
        """Slot for *ChannelDiscoveryWorker* 'finished' signal."""
        self.set_loading_state(False, "Discovery complete.")

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

    def on_channel_source_result(self, content_string: str):
        """Slot for ChannelSourceWorker 'result' signal."""
        self.channel_source_viewer.setPlainText(content_string)

    def on_extraction_result(self, result: ExtractionResult):
        """Slot for ExtractionWorker 'result' signal."""
        import json

        if result.error:
            error_msg = f"--- EXTRACTION FAILED ---\n\n{result.error}"
            self.markdown_output_view.setPlainText(error_msg)
            self.metadata_output_view.setPlainText(error_msg)
            self.append_log_history(f"[Error] Extraction failed: {result.error}")
        else:
            # Set Markdown content
            self.markdown_output_view.setPlainText(result.markdown_content or "[No Markdown Content Extracted]")

            # Set Metadata content (as pretty-printed JSON)
            try:
                metadata_str = json.dumps(
                    result.metadata,
                    indent=2,
                    ensure_ascii=False,
                    default=str  # Handle non-serializable types like datetime
                )
                self.metadata_output_view.setPlainText(metadata_str)
            except Exception as e:
                self.metadata_output_view.setPlainText(f"Could not serialize metadata: {e}\n\n{result.metadata}")

    def on_extraction_finished(self):
        """Slot for *ExtractionWorker* 'finished' signal."""
        self.set_loading_state(False, "Extraction complete.")

    def on_worker_finished(self):
        """Generic 'finished' slot for sub-tasks."""
        if not self.analyze_button.isEnabled():
            if self.thread_pool.activeThreadCount() == 0:
                self.status_bar.showMessage("Task complete. Ready.", 3000)

    def on_worker_error(self, error: tuple):
        """Slot for any worker's 'error' signal."""
        ex_type, message, tb = error
        error_msg = f"Error: {ex_type}: {message}"
        self.status_bar.showMessage(error_msg)

        if self.log_history_view:
            self.log_history_view.append(f"--- Worker Error ---")
            self.log_history_view.append(error_msg)
            self.log_history_view.append(tb)
            self.log_history_view.append(f"--------------------")

        print(f"--- Worker Error ---")
        print(tb)
        print(f"--------------------")

        # Re-enable UI if a main task fails
        self.set_loading_state(False, f"Error occurred. {message}")

    # --- UI Event Handlers ---

    def on_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handles clicks on any tree item (channel or article)."""
        if not self.tree_widget.isEnabled(): return
        data = item.data(0, Qt.UserRole)
        if not data: return

        item_type = data.get('type')
        url = data.get('url')

        if item_type == 'channel':
            if item.childCount() == 1 and "Loading" in item.child(0).text(0):
                return
            if data.get('loaded') == False:
                self.start_article_loading(item, channel_url=url)
            self.start_channel_source_loading(url=url)

        elif item_type == 'article':
            # --- REQ 2a: Update URL bar ---
            self.article_url_input.setText(url)
            self.markdown_output_view.clear()
            self.update_generated_code()

            if self.web_view and QUrl:
                self.web_view.setUrl(QUrl(url))
                self.web_view.setFocus()
                self.tab_widget.setCurrentWidget(self.article_preview_widget)
                self.status_bar.showMessage(f"Loading page: {url}", 3000)

    def on_article_go_clicked(self):
        """Handles clicks on the 'Go' button in the article tab."""
        if self.web_view and QUrl:
            url = self.article_url_input.text()
            self.web_view.setUrl(QUrl(url))
            self.web_view.setFocus()

    def update_generated_code_from_tree(self, item: QTreeWidgetItem, column: int):
        """Wrapper to call code gen when tree checkstate changes."""
        data = item.data(0, Qt.UserRole)
        if data and data.get('type') == 'channel':
            self.update_generated_code()

    # --- REQ 5: Code Generation ---
    def update_generated_code(self):
        """
        Orchestrator for code generation.
        Gathers all UI settings into a config dict, then generates
        the corresponding Python code script.
        (代码生成的协调器。
         将所有UI设置收集到一个配置字典中，然后生成相应的Python代码脚本。)
        """
        try:
            # Step 1: Read all UI controls into a structured dictionary
            # (第 1 步：将所有 UI 控件读入结构化字典)
            config_dict = self._build_config_dict()

            # Step 2: Pass the dictionary to the code generator
            # (第 2 步：将字典传递给代码生成器)
            code_script = self.generate_code_from_config(config_dict)

            # Step 3: Display the generated code
            # (第 3 步：显示生成的代码)
            self.generated_code_text.setPlainText(code_script)

        except Exception as e:
            # Show any error during generation in the code block itself
            # (在代码块本身中显示生成期间的任何错误)
            error_msg = f"# Failed to generate code:\n# {type(e).__name__}: {e}\n\n"
            error_msg += traceback.format_exc()
            self.generated_code_text.setPlainText(error_msg)

    def _get_current_extractor_args(self, extractor_name: str) -> dict:
        """
        Placeholder for retrieving extractor-specific arguments.
        (TODO: Implement this when the 'Settings' button is functional).
        (用于检索提取器特定参数的占位符。
         （TODO：在“设置”按钮可用时实现此功能）。)
        """
        if extractor_name == "Generic CSS":
            # Hardcoded example for now
            return {
                'selectors': ['article', '.content'],
                'exclude_selectors': ['nav', 'footer']
            }
        return {}  # Default

    def _build_config_dict(self) -> dict:
        """
        Reads all UI controls and builds the standardized config dictionary.
        (读取所有UI控件并构建标准化的配置字典。)
        """
        # --- 1. Discoverer Configuration ---
        discovery_fetcher_name = self.discovery_fetcher_combo.currentText()
        discoverer_fetcher_params = {
            "class": discovery_fetcher_name,
            "parameters": {
                "proxy": self.discovery_proxy_input.text().strip() or None,
                "timeout": self.discovery_timeout_spin.value(),
                "stealth": "Stealth" in discovery_fetcher_name,
                "pause_browser": self.pause_browser_check.isChecked(),
                "render_page": False  # Hardcoded False for discovery
            }
        }

        discoverer_name = self.discoverer_combo.currentText()
        discoverer_args = {
            "entry_point_url": self.url_input.currentText().strip(),
            # --- MODIFICATION: Store new date filter state ---
            "date_filter_enabled": self.date_filter_check.isChecked(),
            "date_filter_days": self.date_filter_days_spin.value(),
        }

        # --- 2. Extractor Configuration ---
        article_fetcher_name = self.article_fetcher_combo.currentText()
        extractor_fetcher_params = {
            "class": article_fetcher_name,
            "parameters": {
                "proxy": self.article_proxy_input.text().strip() or None,
                "timeout": self.article_timeout_spin.value(),
                "stealth": "Stealth" in article_fetcher_name,
                "pause_browser": self.article_pause_check.isChecked(),
                "render_page": self.article_render_check.isChecked()
            }
        }

        extractor_name = self.extractor_combo.currentText()
        extractor_args = self._get_current_extractor_args(extractor_name)

        # --- 3. Assemble Final Config ---
        config = {
            "discoverer": {
                "class": discoverer_name,
                "args": discoverer_args,
                "fetcher": discoverer_fetcher_params
            },
            "extractor": {
                "class": extractor_name,
                "args": extractor_args,
                "fetcher": extractor_fetcher_params
                # --- MODIFICATION: URL removed (Request 2) ---
                # It will now be sourced from the discovery pipeline
            }
        }
        return config

    def generate_code_from_config(self, config: dict) -> str:
        """
        Takes a configuration dict and generates a single, pipelined,
        runnable Python script.
        (获取配置字典并生成一个单一的、管道化的、可运行的Python脚本。)
        """

        # --- 1. Class Name Mappings (The "Table" Lookup) ---
        DISCOVERER_CLASS_MAP = {"Sitemap": "SitemapDiscoverer", "RSS": "RSSDiscoverer"}
        FETCHER_CLASS_MAP = {
            "Simple (Requests)": "RequestsFetcher",
            "Advanced (Playwright)": "PlaywrightFetcher",
            "Stealth (Playwright)": "PlaywrightFetcher"
        }

        # --- 2. Get Discovery Config ---
        d_config = config['discoverer']
        d_fetcher_config = d_config['fetcher']
        d_class_name = DISCOVERER_CLASS_MAP.get(d_config['class'], "UnknownDiscoverer")
        d_fetcher_class_name = FETCHER_CLASS_MAP.get(d_fetcher_config['class'], "UnknownFetcher")

        d_fetcher_params = d_fetcher_config['parameters'].copy()
        if 'Playwright' in d_fetcher_class_name:
            d_fetcher_params['timeout'] = d_fetcher_params.get('timeout', 10) * 1000
        d_fetcher_args_str = f"log_callback=log_cb, " + ", ".join(f"{k}={repr(v)}" for k, v in d_fetcher_params.items())

        # --- 3. Get Extraction Config ---
        e_config = config['extractor']
        e_fetcher_config = e_config['fetcher']
        e_class_name = "UnknownExtractor"
        if e_config['class'] in EXTRACTOR_MAP:
            e_class_name = EXTRACTOR_MAP[e_config['class']].__name__
        e_fetcher_class_name = FETCHER_CLASS_MAP.get(e_fetcher_config['class'], "UnknownFetcher")

        e_fetcher_params = e_fetcher_config['parameters'].copy()
        if 'Playwright' in e_fetcher_class_name:
            e_fetcher_params['timeout'] = e_fetcher_params.get('timeout', 20) * 1000
        e_fetcher_args_str = f"log_callback=log_cb, " + ", ".join(f"{k}={repr(v)}" for k, v in e_fetcher_params.items())

        e_kwargs_str = repr(e_config['args'])  # Extractor-specific args

        # --- 4. Build the Pipelined Code String ---
        code = "# === Imports ===\n"
        code += "import datetime\n"
        code += "import json\n"
        code += "import time\n"
        code += "from IntelligenceCrawler.Fetcher import *\n"
        code += "from IntelligenceCrawler.Discoverer import *\n"
        code += "from IntelligenceCrawler.Extractor import *\n\n"
        code += "log_cb = print\n\n"

        code += "# === Main Pipeline Function ===\n"
        code += "def run_full_pipeline():\n"
        code += "    d_fetcher = None\n"
        code += "    e_fetcher = None\n"
        code += "    total_articles_processed = 0\n"
        code += "    try:\n"

        # --- Part 1: Discovery ---
        code += "        # --- 1. Initialize Discovery Components ---\n"
        code += f"        d_fetcher = {d_fetcher_class_name}({d_fetcher_args_str})\n"
        code += f"        discoverer = {d_class_name}(fetcher=d_fetcher, verbose=True)\n\n"

        code += "        # --- 2. Run Discovery ---\n"

        # --- MODIFICATION: New Date Logic (Request 1) ---
        if d_config['args']['date_filter_enabled']:
            code += "        print(\"Applying date filter...\")\n"
            code += f"        days_ago = {d_config['args']['date_filter_days']}\n"
            code += "        end_date = datetime.datetime.now()\n"
            code += "        start_date = end_date - datetime.timedelta(days=days_ago)\n"
        else:
            code += "        print(\"No date filter applied.\")\n"
            code += "        start_date = None\n"
            code += "        end_date = None\n"

        code += "        channels = discoverer.discover_channels(\n"
        code += f"            entry_point_url={repr(d_config['args']['entry_point_url'])},\n"
        code += "            start_date=start_date,\n"
        code += "            end_date=end_date\n"
        code += "        )\n"
        code += "        print(f\"Found {len(channels)} channels to process.\")\n\n"

        # --- Part 2: Extraction (Pipelined) ---
        code += "        # --- 3. Initialize Extraction Components ---\n"
        code += f"        e_fetcher = {e_fetcher_class_name}({e_fetcher_args_str})\n"
        code += f"        extractor = {e_class_name}(verbose=True)\n"
        code += f"        extractor_kwargs = {e_kwargs_str}\n\n"

        code += "        # --- 4. Run Extraction Pipeline ---\n"
        code += "        for channel_url in channels:\n"
        code += "            print(f\"--- Processing Channel: {channel_url} ---\")\n"
        code += "            articles = discoverer.get_articles_for_channel(channel_url)\n"
        code += "            print(f\"Found {len(articles)} articles in channel.\")\n\n"
        code += "            for article_url in articles:\n"
        code += "                try:\n"
        code += "                    print(f\"Extracting: {article_url}\")\n"
        code += "                    content = e_fetcher.get_content(article_url)\n"
        code += "                    if not content:\n"
        code += "                        print(f\"Skipped (no content): {article_url}\")\n"
        code += "                        continue\n\n"
        code += "                    result = extractor.extract(content, article_url, **extractor_kwargs)\n"
        code += "                    total_articles_processed += 1\n"

        code += "                    # --- Your processing logic here --- \n"
        code += "                    # print(result.markdown_content)\n"
        code += "                    # print(json.dumps(result.metadata, default=str))\n"
        code += "                    # time.sleep(1) # Be polite\n\n"

        code += "                except Exception as e:\n"
        code += "                    print(f\"Failed to extract {article_url}: {e}\")\n\n"

        code += "            print(f\"--- Finished Channel: {channel_url} ---\")\n"

        code += "    except Exception as e:\n"
        code += "        print(f\"A critical error occurred: {e}\")\n"
        code += "        import traceback\n"
        code += "        traceback.print_exc()\n"
        code += "    finally:\n"
        code += "        # --- 5. Cleanup --- \n"
        code += "        print(\"--- Cleaning up fetchers ---\")\n"
        code += "        if d_fetcher: d_fetcher.close()\n"
        code += "        if e_fetcher: e_fetcher.close()\n"
        code += "        print(f\"Pipeline finished. Processed {total_articles_processed} articles.\")\n\n"

        code += "if __name__ == \"__main__\":\n"
        code += "    run_full_pipeline()\n"

        return code

    def closeEvent(self, event):
        """Ensure threads are cleaned up on exit."""
        self.status_bar.showMessage("Shutting down... waiting for tasks...")
        self.thread_pool.waitForDone(3000)
        self.thread_pool.clear()
        event.accept()

    # --- NEW: URL History Management Methods ---

    def _load_url_history(self):
        """Loads URL history from QSettings into the ComboBox."""
        settings = QSettings("MyOrg", "CrawlerPlayground")
        history = settings.value(self.URL_HISTORY_KEY, [], type=list)
        if history:
            self.url_input.addItems(history)
            self.url_input.setCurrentIndex(-1)  # Show placeholder

    def _save_url_history(self, url: str):
        """Saves a new URL to the top of the history and QSettings."""
        if not url:
            return

        # 1. Find if item already exists
        found_index = self.url_input.findText(url, Qt.MatchFixedString)

        # 2. Remove if exists
        if found_index >= 0:
            self.url_input.removeItem(found_index)

        # 3. Add to top
        self.url_input.insertItem(0, url)
        self.url_input.setCurrentText(url)  # Ensure it's the selected item

        # 4. Trim history if over limit
        while self.url_input.count() > self.MAX_URL_HISTORY:
            self.url_input.removeItem(self.MAX_URL_HISTORY)

        # 5. Persist to QSettings
        new_history = [self.url_input.itemText(i) for i in range(self.url_input.count())]
        settings = QSettings("MyOrg", "CrawlerPlayground")
        settings.setValue(self.URL_HISTORY_KEY, new_history)

    def _show_url_history_context_menu(self, pos):
        """Shows a right-click context menu for the URL ComboBox."""
        menu = QMenu(self)
        clear_action = menu.addAction("Clear History")

        action = menu.exec_(self.url_input.mapToGlobal(pos))

        if action == clear_action:
            self._clear_url_history()

    def _clear_url_history(self):
        """Clears the ComboBox and the QSettings history."""
        self.url_input.clear()  # Clears the list
        self.url_input.clearEditText()  # Clears the typed text

        settings = QSettings("MyOrg", "CrawlerPlayground")
        settings.setValue(self.URL_HISTORY_KEY, [])
        self.status_bar.showMessage("URL history cleared.")


# =============================================================================
#
# SECTION 4: Main Execution
#
# =============================================================================

if __name__ == "__main__":
    if not QWebEngineView:
        print("\n--- WARNING ---")
        print("PyQtWebEngine not found. The Article web preview will be disabled.")
        print("Please install it for full functionality: pip install PyQtWebEngine")

    if not sync_playwright:
        print("\n--- WARNING ---")
        print("Playwright not found. 'Advanced' and 'Stealth' fetchers will be disabled.")
        print("Please install it: pip install playwright && python -m playwright install")

    app = QApplication(sys.argv)

    app.setOrganizationName("SleepySoft")
    app.setApplicationName("CrawlerPlayground")

    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    main_window = CrawlerPlaygroundApp()  # Renamed
    main_window.show()

    sys.exit(app.exec_())
