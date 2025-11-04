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
    QDateEdit, QCheckBox, QToolBar, QSizePolicy
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
                            proxy: Optional[str] = None,  # <-- NEW
                            **kwargs) -> Fetcher:
    """Factory to create a fetcher instance based on its name."""
    stealth_mode = "Stealth" in fetcher_name
    pause = kwargs.get('pause_browser', False)
    render = kwargs.get('render_page', False)

    if "Playwright" in fetcher_name:
        if not sync_playwright: raise ImportError("Playwright not installed.")
        if stealth_mode and (not sync_stealth and not Stealth):
            raise ImportError("Playwright-Stealth not installed.")
        return PlaywrightFetcher(
            log_callback=log_callback,
            proxy=proxy,  # <-- NEW
            stealth=stealth_mode,
            pause_browser=pause,
            render_page=render
        )
    else:  # "Simple (Requests)"
        return RequestsFetcher(
            log_callback=log_callback,
            proxy=proxy  # <-- NEW
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
                 proxy: Optional[str],  # <-- NEW
                 pause_browser: bool,
                 render_page: bool):
        super(ChannelDiscoveryWorker, self).__init__()
        self.discoverer_name = discoverer_name
        self.fetcher_name = fetcher_name
        self.homepage_url = homepage_url
        self.start_date = start_date
        self.end_date = end_date
        self.proxy = proxy
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
                 pause_browser: bool,
                 render_page: bool):
        super(ArticleListWorker, self).__init__()
        self.discoverer_name = discoverer_name
        self.fetcher_name = fetcher_name
        self.channel_url = channel_url
        self.proxy = proxy
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
                 pause_browser: bool,
                 render_page: bool):
        super(ChannelSourceWorker, self).__init__()
        self.discoverer_name = discoverer_name
        self.fetcher_name = fetcher_name
        self.url = url
        self.proxy = proxy
        self.pause_browser = pause_browser
        self.render_page = render_page
        self.signals = WorkerSignals()

    def run(self):
        fetcher: Optional[Fetcher] = None
        try:
            log_callback = self.signals.progress.emit

            # 1. Create Fetcher
            if self.render_page:
                log_callback("[Warning] 'Render Page' is enabled for Channel Source, " \
                             "this may fail XML/RSS parsing. Forcing False.")

            fetcher = create_fetcher_instance(
                self.fetcher_name,
                log_callback,
                proxy=self.proxy,
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
                 pause_browser: bool,
                 render_page: bool):
        super(ExtractionWorker, self).__init__()
        self.fetcher_name = fetcher_name
        self.extractor_name = extractor_name
        self.url_to_extract = url_to_extract
        self.extractor_kwargs = extractor_kwargs
        self.proxy = proxy
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

        # --- Initialize UI ---
        self.init_ui()
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
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter website homepage URL (e.g., https://www.example.com)")
        top_bar_layout.addWidget(self.url_input, 1)

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

        # --- Date Period Selectors (Original) ---
        top_bar_layout.addWidget(QLabel("From:"))
        self.start_date_edit = QDateEdit(QDate.currentDate().addDays(-7))
        self.start_date_edit.setCalendarPopup(True)
        top_bar_layout.addWidget(self.start_date_edit)

        top_bar_layout.addWidget(QLabel("To:"))
        self.end_date_edit = QDateEdit(QDate.currentDate())
        self.end_date_edit.setCalendarPopup(True)
        top_bar_layout.addWidget(self.end_date_edit)

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

        # --- NEW: Discovery Proxy Input ---
        top_bar_layout.addWidget(QLabel("Proxy:"))
        self.discovery_proxy_input = QLineEdit()
        self.discovery_proxy_input.setPlaceholderText("e.g., http://user:pass@host:port")
        # Give it a small stretch factor to fill remaining space
        self.discovery_proxy_input.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
        top_bar_layout.addWidget(self.discovery_proxy_input, 1)  # 1 stretch

        # --- Analyze Button (Original) ---
        self.analyze_button = QPushButton("Discover Channels")  # Renamed
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

        # Add a spacer to push all fetcher controls to the left
        fetcher_spacer = QWidget()
        fetcher_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        fetcher_toolbar.addWidget(fetcher_spacer)

        # --- Toolbar 2: Extractor Settings ---
        extractor_toolbar = QToolBar("Extractor Tools")
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
        self.url_input.returnPressed.connect(self.start_channel_discovery)
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
        self.start_date_edit.setEnabled(not is_loading)
        self.end_date_edit.setEnabled(not is_loading)
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
        url = self.url_input.text().strip()
        if not url:
            self.status_bar.showMessage("Error: Please enter a URL.")
            return

        if not url.startswith("http"):
            url = "https://" + url
            self.url_input.setText(url)

        self.clear_all_controls()

        # Get values from UI
        start_date = self.start_date_edit.dateTime().toPyDateTime()
        end_date_qdt = self.end_date_edit.dateTime()
        end_date = end_date_qdt.toPyDateTime().replace(hour=23, minute=59, second=59)

        # Store the selected strategy names and options
        self.discoverer_name = self.discoverer_combo.currentText()
        self.discovery_fetcher_name = self.discovery_fetcher_combo.currentText()
        self.pause_browser = self.pause_browser_check.isChecked()
        self.render_page = self.render_page_check.isChecked()

        self.set_loading_state(True, f"Discovering {self.discoverer_name} channels for {url}...")
        self.update_generated_code()  # Update code snippet

        proxy_str = self.discovery_proxy_input.text().strip() or None

        worker = ChannelDiscoveryWorker(
            discoverer_name=self.discoverer_name,
            fetcher_name=self.discovery_fetcher_name,
            homepage_url=url,
            start_date=start_date,
            end_date=end_date,
            proxy=proxy_str,  # <-- NEW
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

        worker = ArticleListWorker(
            discoverer_name=self.discoverer_name,
            fetcher_name=self.discovery_fetcher_name,
            channel_url=channel_url,
            proxy=proxy_str,  # <-- NEW
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

        worker = ChannelSourceWorker(
            discoverer_name=self.discoverer_name,
            fetcher_name=self.discovery_fetcher_name,
            url=url,
            proxy=proxy_str,  # <-- NEW
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

        worker = ExtractionWorker(
            fetcher_name=fetcher_name,
            extractor_name=extractor_name,
            url_to_extract=url,
            extractor_kwargs=extractor_kwargs,
            proxy=proxy_str,  # <-- NEW
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
        """Generates Python code snippets based on current UI settings."""

        # Part 1: Discovery Code
        discover_name = self.discoverer_combo.currentText()
        fetcher_name = self.discovery_fetcher_combo.currentText()
        pause = self.pause_browser_check.isChecked()
        render = self.render_page_check.isChecked()  # This is the main window's
        discovery_proxy = self.discovery_proxy_input.text().strip() or None
        url = self.url_input.text()
        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")

        code = "from IntelligenceCrawler.Fetcher import *\n"
        code += "from IntelligenceCrawler.Discoverer import *\n"
        code += "import datetime\n\n"
        code += "# --- Part 1: Discovery ---\n"
        code += f"discoverer_name = \"{discover_name}\"\n"
        code += f"fetcher_name = \"{fetcher_name}\"\n"
        code += f"homepage_url = \"{url}\"\n"
        code += f"pause_browser = {pause}\n"
        code += "# Note: 'render_page' is forced False for Discovery workers.\n"
        code += f"start_date = datetime.datetime.strptime(\"{start_date}\", \"%Y-%m-%d\")\n"
        code += f"end_date = datetime.datetime.strptime(\"{end_date}\", \"%Y-%m-%d\").replace(hour=23, minute=59)\n\n"
        code += "log_cb = print  # Use print for logging\n"
        code += f"discovery_proxy = {repr(discovery_proxy)}\n"
        code += "fetcher = create_fetcher_instance(fetcher_name, log_cb, " \
                f"proxy=discovery_proxy, pause_browser={pause}, render_page=False)\n"
        code += "discoverer = create_discoverer_instance(discoverer_name, fetcher, log_cb)\n"
        code += "channels = discoverer.discover_channels(homepage_url, start_date, end_date)\n"
        code += "print(f\"Found {len(channels)} channels.\")\n"

        # Add channel filter code
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

        if selected_paths:
            code += "\n# --- Channel Filtering (based on tree selection) ---\n"
            code += "SELECTED_CHANNEL_PATHS = {\n"
            for path in sorted(selected_paths):
                code += f"    \"{path}\",\n"
            code += "}\n"
            code += "filtered_channels = [c for c in channels if urlparse(c).path in SELECTED_CHANNEL_PATHS]\n"
            code += "print(f\"Filtered down to {len(filtered_channels)} channels.\")\n"
        else:
            code += "filtered_channels = channels # No filters selected\n"

        code += "# articles = discoverer.get_articles_for_channel(filtered_channels[0])\n"

        # Part 2: Extraction Code
        article_url = self.article_url_input.text()
        article_fetcher = self.article_fetcher_combo.currentText()
        extractor_name = self.extractor_combo.currentText()
        # This is the correct render setting for extraction
        render_for_extraction = self.render_page_check.isChecked()

        code += "\n\n# --- Part 2: Extraction ---\n"
        code += "from IntelligenceCrawler.Extractor import *\n\n"

        code += f"article_url = \"{article_url}\"\n"
        code += f"article_fetcher_name = \"{article_fetcher}\"\n"
        code += f"extractor_name = \"{extractor_name}\"\n"

        # --- Read from the article tab's checkboxes ---
        pause_for_extraction = self.article_pause_check.isChecked()
        article_proxy = self.article_proxy_input.text().strip() or None

        code += f"pause_for_extraction = {pause_for_extraction}\n"
        code += f"render_for_extraction = {render_for_extraction}\n"
        code += f"article_proxy = {repr(article_proxy)}\n"

        # TODO: Get this from settings dialog
        extractor_kwargs = {}
        if extractor_name == "Generic CSS":
            extractor_kwargs = {'selectors': ['article', '.content'], 'exclude_selectors': ['nav', 'footer']}

        code += f"extractor_kwargs = {extractor_kwargs}\n\n"
        code += "article_fetcher = create_fetcher_instance(article_fetcher_name, log_cb, " \
                f"proxy=article_proxy, pause_browser={pause_for_extraction}, " \
                f"render_page={render_for_extraction})\n"
        code += "content = article_fetcher.get_content(article_url)\n"
        code += "extractor = create_extractor_instance(extractor_name, log_cb)\n"
        code += "markdown = extractor.extract(content, article_url, **extractor_kwargs)\n"
        code += "print(f\"--- Extracted Markdown ---\n{markdown}\")\n\n"
        code += "article_fetcher.close()\n"
        code += "fetcher.close()\n"

        self.generated_code_text.setPlainText(code)

    def closeEvent(self, event):
        """Ensure threads are cleaned up on exit."""
        self.status_bar.showMessage("Shutting down... waiting for tasks...")
        self.thread_pool.waitForDone(3000)
        self.thread_pool.clear()
        event.accept()


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

    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    main_window = CrawlerPlaygroundApp()  # Renamed
    main_window.show()

    sys.exit(app.exec_())
