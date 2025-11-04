#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import traceback
from typing import Dict, Optional
from urllib.parse import urlparse
from abc import ABC, abstractmethod


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


class Fetcher(ABC):
    """
    Abstract Base Class for a content fetcher.
    Defines the interface for different fetching strategies (e.g., simple requests
    or full browser rendering) and standardizes how they are initialized and used.
    """

    @abstractmethod
    def get_content(self, url: str) -> Optional[bytes]:
        """
        Fetches content from a given URL.

        Args:
            url (str): The URL to fetch.

        Returns:
            Optional[bytes]: The raw content of the response as bytes,
                             or None if fetching failed.
        """
        pass

    @abstractmethod
    def close(self):
        """
        Cleans up any persistent resources.
        This could be a requests.Session, a Playwright browser instance,
        or any other long-lived connection.
        """
        pass


def also_print(log_callback):
    """A helper wrapper to ensure logs are always printed to console."""

    def wrapper(text):
        if log_callback != print:
            print(text)
        log_callback(text)

    return wrapper


class RequestsFetcher(Fetcher):
    """
    A fast, lightweight fetcher that uses the `requests` library.
    It maintains a persistent `requests.Session` for connection pooling
    and cookie handling.

    This fetcher is ideal for simple websites, APIs, XML sitemaps, and
    other resources that do not require JavaScript rendering.
    """
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
    }

    def __init__(self,
                 log_callback=print,
                 proxy: Optional[str] = None,
                 timeout_s: int = 10):
        """
        Initializes the RequestsFetcher.

        Args:
            log_callback: A callable (like print) to receive log messages.
            proxy (Optional[str]): A proxy URL string.
                Format: "protocol://user:pass@host:port"
                Examples:
                    - "http://127.0.0.1:8080"
                    - "http://user:pass@proxyserver.com:8080"
                    - "socks5://user:pass@127.0.0.1:1080"
                (For SOCKS support, `pip install "requests[socks]"` is required)
        """
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self._log = also_print(log_callback)
        self.timeout = timeout_s

        # --- NEW: Proxy Configuration ---
        if proxy:
            # `requests` expects a dictionary mapping protocols to the proxy URL.
            # We use the same proxy string for both http and https traffic.
            proxies = {
                'http': proxy,
                'https': proxy
            }
            self.session.proxies.update(proxies)

            # Log the proxy server, but hide credentials for security.
            proxy_host = proxy.split('@')[-1]
            self._log(f"Using RequestsFetcher with proxy: {proxy_host}")
        else:
            self._log("Using RequestsFetcher (Fast, Simple)")

    def get_content(self, url: str) -> Optional[bytes]:
        """
        Fetches content from a URL using the configured requests.Session.

        Args:
            url (str): The URL to fetch.

        Returns:
            Optional[bytes]: The raw response content, or None on failure.
        """
        try:
            # Set a dynamic Referer header based on the target domain
            parsed_url = urlparse(url)
            referer = f"{parsed_url.scheme}://{parsed_url.netloc}/"

            response = self.session.get(
                url,
                timeout=self.timeout,
                headers={'Referer': referer}
            )
            response.raise_for_status()  # Raise an HTTPError for bad responses (4xx or 5xx)
            return response.content
        except requests.exceptions.RequestException as e:
            self._log(f"[Request Error] Failed to fetch {url}: {e}")
            return None

    def close(self):
        """Closes the persistent requests.Session."""
        self._log("Closing RequestsFetcher session.")
        self.session.close()


class PlaywrightFetcher(Fetcher):
    """
    A robust, slower fetcher that uses a real headless browser (Playwright)
    to render pages, execute JavaScript, and bypass anti-bot measures.

    It can be configured in two main modes:
      1. 'Advanced' (stealth=False): Applies only a basic 'webdriver' patch.
      2. 'Stealth' (stealth=True): Applies the full 'playwright-stealth'
         patch suite to appear more human.
    """

    def __init__(self,
                 log_callback=print,
                 proxy: Optional[str] = None,
                 timeout_s: int = 20,
                 stealth: bool = False,
                 pause_browser: bool = False,
                 render_page: bool = True):
        """
        Initializes the PlaywrightFetcher and starts the browser instance.

        Args:
            log_callback:
                A callable (like print) to receive log messages.
            proxy (Optional[str]): A proxy URL string.
                Format: "protocol://user:pass@host:port"
                Examples:
                    - "http://127.0.0.1:8080"
                    - "socks5://127.0.0.1:1080" (Playwright supports SOCKS5)
            stealth:
                If True, apply full 'playwright-stealth' patches.
                If False, apply only the basic 'webdriver' patch.
            pause_browser:
                If True, launches in 'headful' mode (not headless) and
                calls `page.pause()` after navigation for debugging.
            render_page:
                If True (Default), returns the final rendered HTML (`page.content()`).
                If False, returns the raw network response (`response.body()`).
                **Set to False to correctly download raw XML/JSON files.**
        """
        self._log = also_print(log_callback)
        self.timeout=timeout_s * 1000           # Playwright timeout is in ms
        self.stealth_mode = stealth
        self.pause_browser = pause_browser
        self.render_page = render_page
        self.proxy_config: Optional[Dict[str, str]] = None  # Store for later use

        # --- 1. Verify Library Availability ---
        if not sync_playwright:
            raise ImportError("Playwright is not installed.")
        if self.stealth_mode and (not sync_stealth and not Stealth):
            raise ImportError("Playwright-Stealth (v1 or v2) is not installed.")

        # --- NEW: Parse Proxy Configuration ---
        if proxy:
            try:
                parsed_proxy = urlparse(proxy)
                if not all([parsed_proxy.scheme, parsed_proxy.hostname, parsed_proxy.port]):
                    raise ValueError("Proxy string must include scheme, host, and port.")

                # Playwright expects a dictionary of proxy components
                self.proxy_config = {
                    "server": f"{parsed_proxy.scheme}://{parsed_proxy.hostname}:{parsed_proxy.port}"
                }
                # Add credentials if they exist in the proxy URL
                if parsed_proxy.username:
                    self.proxy_config["username"] = parsed_proxy.username
                if parsed_proxy.password:
                    self.proxy_config["password"] = parsed_proxy.password

                self._log(f"Playwright proxy configured for server: {self.proxy_config['server']}")
            except Exception as e:
                # If parsing fails, log a warning and continue without a proxy
                self._log(f"!!! WARNING: Invalid proxy format '{proxy}'. Ignoring proxy. Error: {e}")
                self.proxy_config = None

        # --- 2. Start Playwright and Launch Browser ---
        try:
            mode = "Stealth" if self.stealth_mode else "Advanced"
            self._log(f"Starting PlaywrightFetcher ({mode}, Slow)...")

            # Browser is "headful" (visible) only if debugging
            headless_mode = not self.pause_browser

            self.playwright = sync_playwright().start()

            # Note: We do NOT apply the proxy at launch.
            # We apply it at the `new_context` level in `get_content`.
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
        Fetches content from a URL using a new, isolated browser context.

        Args:
            url (str): The URL to fetch.

        Returns:
            Optional[bytes]: The page content (rendered or raw), or None on failure.
        """
        context = None  # Define context in outer scope for 'finally'
        try:
            # --- 1. Create Browser Context and Page ---

            # Build the options for the new browser context
            context_options = {
                "user_agent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
            }

            # --- NEW: Apply Proxy to Context ---
            if self.proxy_config:
                # Apply the parsed proxy settings to this specific context
                context_options["proxy"] = self.proxy_config

            context = self.browser.new_context(**context_options)
            page = context.new_page()

            # --- 2. Apply Browser Patches (Stealth or Basic) ---
            if self.stealth_mode:
                if sync_stealth:
                    # Use v2.x method.
                    self._log("Applying full stealth patches (v2 'sync_stealth(page)')...")
                    # NOTE: This is the correct usage for the v2 fork.
                    sync_stealth(page)
                elif Stealth:
                    # Use v1.x method
                    self._log("Applying full stealth patches (v1 'Stealth.apply_stealth_sync(page)')...")
                    stealth_instance = Stealth()
                    stealth_instance.apply_stealth_sync(page)
                else:
                    # Fallback just in case, though the __init__ check should prevent this
                    self._log("Stealth mode selected but no library found. Applying basic patch.")
                    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            else:
                # "Advanced" mode: Apply only the basic 'webdriver' patch
                self._log("Applying basic 'webdriver' patch...")
                # This script hides the 'navigator.webdriver' flag from the page
                page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            # --- 3. Navigate to the Page ---
            self._log(f"Navigating to {url}...")
            # 'domcontentloaded' is often faster and sufficient
            response = page.goto(url, timeout=self.timeout, wait_until='domcontentloaded')

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
            context.close()  # Close the context to free up resources
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
