#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sitemap Analyzer (PyQt5)

A PyQt5 application to browse and analyze a website's sitemap "channels"
and their corresponding article URLs.

This application uses a two-stage, asynchronous (threaded) loading process
to keep the UI responsive.

Main Features:
1.  Asynchronously discovers all "leaf" sitemaps (channels) from a domain.
2.  Displays channels in a tree view with checkboxes.
3.  Lazy-loads article URLs for a channel *only* when it's clicked.
4.  Displays a preview of the article webpage (QWebEngineView).
5.  Displays the raw XML source of the channel sitemap (QTextEdit).
6.  Auto-generates a Python filter function based on selected channels.

Required libraries:
    pip install PyQt5 PyQtWebEngine requests ultimate-sitemap-parser
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

# --- PyQt5 Imports ---
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QSplitter,
    QTextEdit, QStatusBar, QTabWidget, QLabel, QFrame
)
from PyQt5.QtCore import (
    Qt, QRunnable, QThreadPool, QObject, pyqtSignal, QSize
)
from PyQt5.QtGui import QFont, QIcon

# --- PyQtWebEngine Imports ---
# This is a separate dependency: pip install PyQtWebEngine
try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    from PyQt5.QtCore import QUrl
except ImportError:
    print("Error: PyQtWebEngine not found.")
    print("Please install it: pip install PyQtWebEngine")
    # Fallback to a simple QTextEdit if not found
    QWebEngineView = None
    QUrl = None

from SitemapDiscoverer import SitemapDiscoverer


# =============================================================================
#
# SECTION 2: PyQt5 Threading Workers (QRunnable)
#
# =============================================================================

class WorkerSignals(QObject):
    """
    Defines the signals available from a running worker thread.
    Supported signals are:

    finished: No data
    error:    tuple (str, str) -> (Exception type, Exception message)
    result:   object (any) -> The result of the operation
    """
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(object)


class ChannelDiscoveryWorker(QRunnable):
    """
    Worker thread for Stage 1: Discovering all channels.
    Runs `discoverer.discover_channels()` in the background.
    """

    def __init__(self, discoverer: SitemapDiscoverer, homepage_url: str):
        super(ChannelDiscoveryWorker, self).__init__()
        self.discoverer = discoverer
        self.homepage_url = homepage_url
        self.signals = WorkerSignals()

    def run(self):
        """Execute the task."""
        try:
            channel_list = self.discoverer.discover_channels(self.homepage_url)
            self.signals.result.emit(channel_list)  # Emit the list of channels
        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e)))
        finally:
            self.signals.finished.emit()


class ArticleListWorker(QRunnable):
    """
    Worker thread for Stage 2 (Lazy Loading): Gets articles for one channel.
    Runs `discoverer.get_articles_for_channel()` in the background.
    """

    def __init__(self, discoverer: SitemapDiscoverer, channel_url: str):
        super(ArticleListWorker, self).__init__()
        self.discoverer = discoverer
        self.channel_url = channel_url
        self.signals = WorkerSignals()

    def run(self):
        """Execute the task."""
        try:
            article_list = self.discoverer.get_articles_for_channel(self.channel_url)
            # Emit a dict to identify which channel these articles belong to
            self.signals.result.emit({
                'channel_url': self.channel_url,
                'articles': article_list
            })
        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e)))
        finally:
            self.signals.finished.emit()


class XmlContentWorker(QRunnable):
    """
    Worker thread to fetch raw XML content for the text viewer.
    Runs `discoverer.get_xml_content_str()` in the background.
    """

    def __init__(self, discoverer: SitemapDiscoverer, url: str):
        super(XmlContentWorker, self).__init__()
        self.discoverer = discoverer
        self.url = url
        self.signals = WorkerSignals()

    def run(self):
        """Execute the task."""
        try:
            xml_string = self.discoverer.get_xml_content_str(self.url)
            self.signals.result.emit(xml_string)
        except Exception as e:
            ex_type, ex_value, tb_str = sys.exc_info()
            self.signals.error.emit((str(ex_type), str(e)))
        finally:
            self.signals.finished.emit()


# =============================================================================
#
# SECTION 3: PyQt5 Main Application
#
# =============================================================================

class SitemapAnalyzerApp(QMainWindow):
    """Main application window for the Sitemap Analyzer."""

    def __init__(self):
        super().__init__()

        # --- Internal State ---
        self.discoverer = SitemapDiscoverer(verbose=False)  # GUI will show logs
        self.thread_pool = QThreadPool()
        # {channel_url: QTreeWidgetItem} mapping
        self.channel_item_map: Dict[str, QTreeWidgetItem] = {}

        # --- Initialize UI ---
        self.init_ui()
        self.setWindowTitle("Sitemap Channel Analyzer")
        self.setWindowIcon(QIcon.fromTheme("internet-web-browser"))  # Basic icon
        self.setGeometry(100, 100, 1200, 800)

    def init_ui(self):
        """Set up the main UI layout."""

        # --- Main Widget and Layout ---
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # --- 1. Top URL Input Bar ---
        top_bar_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter website homepage URL (e.g., https://www.example.com)")
        self.url_input.returnPressed.connect(self.start_channel_discovery)  # Allow pressing Enter
        top_bar_layout.addWidget(self.url_input, 1)

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

        # Tab 1: Article Preview (Web Engine)
        if QWebEngineView:
            self.web_view = QWebEngineView()
            self.tab_widget.addTab(self.web_view, "Article Preview")
        else:
            self.web_view = QTextEdit("QWebEngineView not available. Install PyQtWebEngine.")
            self.web_view.setReadOnly(True)
            self.tab_widget.addTab(self.web_view, "Article Preview (Unavailable)")

        # Tab 2: Sitemap XML Source
        self.xml_viewer = QTextEdit()
        self.xml_viewer.setReadOnly(True)
        self.xml_viewer.setFont(QFont("Courier", 10))
        self.xml_viewer.setLineWrapMode(QTextEdit.NoWrap)
        self.tab_widget.addTab(self.xml_viewer, "Sitemap XML Source")

        self.main_splitter.addWidget(self.tab_widget)
        self.main_splitter.setSizes([350, 850])  # Initial sizing

        main_layout.addWidget(self.main_splitter, 1)  # Give splitter expandable space

        # --- 3. Bottom: Python Filter Code ---
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
        self.filter_code_text.setFixedHeight(150)  # Fixed height
        filter_layout.addWidget(self.filter_code_text)

        main_layout.addWidget(filter_box)

        # --- 4. Status Bar ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready. Enter a URL to begin.")

        self.setCentralWidget(main_widget)

        # --- Initialize text ---
        self.update_filter_code()

    def set_loading_state(self, is_loading: bool, message: str = ""):
        """Enable/Disable UI controls during threaded operations."""
        self.url_input.setEnabled(not is_loading)
        self.analyze_button.setEnabled(not is_loading)
        self.tree_widget.setEnabled(not is_loading)

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
        Slot for 'Analyze' button. Starts the Stage 1 worker.
        """
        url = self.url_input.text().strip()
        if not url:
            self.status_bar.showMessage("Error: Please enter a URL.")
            return

        if not url.startswith("http"):
            url = "https://" + url
            self.url_input.setText(url)

        self.clear_all_controls()
        self.set_loading_state(True, f"Discovering channels for {url}...")

        # Create and start the worker
        worker = ChannelDiscoveryWorker(self.discoverer, url)
        worker.signals.result.connect(self.on_channel_discovery_result)
        worker.signals.finished.connect(self.on_channel_discovery_finished)
        worker.signals.error.connect(self.on_worker_error)
        self.thread_pool.start(worker)

    def start_article_loading(self, channel_item: QTreeWidgetItem, channel_url: str):
        """
        Starts the Stage 2 (Lazy Loading) worker for a specific channel.
        """
        # Show loading indicator in the tree
        channel_item.takeChildren()  # Remove dummy
        loading_item = QTreeWidgetItem(["Loading articles..."])
        channel_item.addChild(loading_item)
        channel_item.setExpanded(True)

        self.status_bar.showMessage(f"Loading articles for {channel_url}...")

        # Create and start the worker
        worker = ArticleListWorker(self.discoverer, channel_url)
        worker.signals.result.connect(self.on_article_list_result)
        worker.signals.error.connect(self.on_worker_error)
        # We don't need a 'finished' signal here, the result handles it
        self.thread_pool.start(worker)

    def start_xml_content_loading(self, url: str):
        """
        Starts the worker to fetch raw XML for the viewer.
        """
        self.xml_viewer.setPlainText(f"Loading XML content from {url}...")
        self.tab_widget.setCurrentWidget(self.xml_viewer)  # Switch to XML tab

        worker = XmlContentWorker(self.discoverer, url)
        worker.signals.result.connect(self.on_xml_content_result)
        worker.signals.error.connect(self.on_worker_error)
        self.thread_pool.start(worker)

    # --- Thread Result Slots ---

    def on_channel_discovery_result(self, channel_list: List[str]):
        """
        Slot for ChannelDiscoveryWorker 'result' signal.
        Populates the L1 tree with discovered channels.
        """
        if not channel_list:
            self.status_bar.showMessage("No sitemap channels (leaf nodes) found.")
            return

        self.tree_widget.setDisabled(True)  # Disable during population
        for channel_url in channel_list:
            item = QTreeWidgetItem([channel_url])
            # Add checkbox
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
            # Store metadata in the item
            item.setData(0, Qt.UserRole, {
                'type': 'channel',
                'url': channel_url,
                'loaded': False  # Flag for lazy loading
            })
            # Add a dummy child to show the [+] expander icon
            item.addChild(QTreeWidgetItem())
            self.tree_widget.addTopLevelItem(item)
            self.channel_item_map[channel_url] = item

        self.tree_widget.setDisabled(False)
        self.status_bar.showMessage(f"Found {len(channel_list)} channels. Click a channel to load articles.")

    def on_channel_discovery_finished(self):
        """Slot for ChannelDiscoveryWorker 'finished' signal."""
        self.set_loading_state(False)

    def on_article_list_result(self, result: Dict[str, Any]):
        """
        Slot for ArticleListWorker 'result' signal.
        Populates L2 (articles) under the correct channel.
        """
        channel_url = result['channel_url']
        article_list = result['articles']

        parent_item = self.channel_item_map.get(channel_url)
        if not parent_item:
            return

        # Update the item's state to 'loaded'
        data = parent_item.data(0, Qt.UserRole)
        data['loaded'] = True
        parent_item.setData(0, Qt.UserRole, data)

        parent_item.takeChildren()  # Remove "Loading..." dummy

        if not article_list:
            parent_item.addChild(QTreeWidgetItem(["No articles found in this channel."]))
        else:
            for article_url in article_list:
                child_item = QTreeWidgetItem([article_url])
                child_item.setData(0, Qt.UserRole, {
                    'type': 'article',
                    'url': article_url
                })
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
        traceback.print_exc()  # Print full stack trace to console
        self.set_loading_state(False, f"Error occurred. {message}")

    # --- UI Event Handlers ---

    def on_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        """
        Handles clicks on any tree item (channel or article).
        """
        data = item.data(0, Qt.UserRole)
        if not data:
            return

        item_type = data.get('type')
        url = data.get('url')

        if item_type == 'channel':
            # --- This is a Channel (L1) ---
            # 1. Start loading its articles (if not already loaded)
            if data.get('loaded') == False:
                self.start_article_loading(item, url)

            # 2. Start loading its XML content for the viewer
            self.start_xml_content_loading(url)

        elif item_type == 'article':
            # --- This is an Article (L2) ---
            if self.web_view and QUrl:
                self.web_view.setUrl(QUrl(url))
                self.web_view.setFocus()
                self.tab_widget.setCurrentWidget(self.web_view)  # Switch to web tab
                self.status_bar.showMessage(f"Loading page: {url}", 3000)

    def on_tree_item_changed(self, item: QTreeWidgetItem, column: int):
        """
        Handles checkbox state changes to update the filter code.
        """
        data = item.data(0, Qt.UserRole)
        if data and data.get('type') == 'channel':
            self.update_filter_code()

    def update_filter_code(self):
        """
        Generates the Python filter code based on checked items.
        """
        selected_paths = []

        # Iterate through all top-level items
        for i in range(self.tree_widget.topLevelItemCount()):
            item = self.tree_widget.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                data = item.data(0, Qt.UserRole)
                if data:
                    # Use the URL's path as a unique, resilient filter key
                    try:
                        path = urlparse(data.get('url')).path
                        selected_paths.append(path)
                    except Exception:
                        pass  # Ignore malformed URLs

        # --- Build the Python code string ---
        header = "# Auto-generated Python filter\n"
        header += "# Use this logic in your script to filter channels.\n\n"
        header += "from urllib.parse import urlparse\n\n"

        if not selected_paths:
            code = "def should_process_channel(channel_url: str) -> bool:\n"
            code += "    # No channels selected. Default to processing nothing.\n"
            code += "    return False\n"
        else:
            code = "SELECTED_CHANNEL_PATHS = {\n"
            for path in sorted(selected_paths):
                code += f"    \"{path}\",\n"
            code += "}\n\n"
            code += "def should_process_channel(channel_url: str) -> bool:\n"
            code += "    \"\"\"Checks if a channel's path is in the selected set.\"\"\"\n"
            code += "    try:\n"
            code += "        path = urlparse(channel_url).path\n"
            code += "        return path in SELECTED_CHANNEL_PATHS\n"
            code += "    except Exception:\n"
            code += "        return False\n"

        self.filter_code_text.setPlainText(header + code)

    def closeEvent(self, event):
        """Ensure threads are cleaned up on exit."""
        # This is a simple app, so we'll just exit.
        # For a complex app, you'd want to signal threads to stop.
        self.thread_pool.clear()  # Removes queued tasks
        event.accept()


# =============================================================================
#
# SECTION 4: Main Execution
#
# =============================================================================

if __name__ == "__main__":
    # --- Set up the PyQt Application ---
    app = QApplication(sys.argv)

    # Enable High-DPI scaling for better visuals
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    # --- Create and Show the Main Window ---
    main_window = SitemapAnalyzerApp()
    main_window.show()

    # --- Start the Event Loop ---
    sys.exit(app.exec_())
