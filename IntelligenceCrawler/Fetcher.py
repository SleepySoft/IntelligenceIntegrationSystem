#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import queue
import requests
import threading        # Add threading for PlaywrightFetcher avoiding asyncio conflict with Newspaper3kExtractor
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
    [Refactored to run in a dedicated thread to avoid asyncio conflicts]

    A robust, slower fetcher that uses a real headless browser (Playwright)
    to render pages, execute JavaScript, and bypass anti-bot measures.

    This class launches Playwright in a separate worker thread,
    and provides a synchronous, thread-safe interface for the main thread.
    """

    def __init__(self,
                 log_callback=print,
                 proxy: Optional[str] = None,
                 timeout_s: int = 20,
                 stealth: bool = False,
                 pause_browser: bool = False,
                 render_page: bool = True):
        """
        Initializes the Fetcher and starts the background Playwright worker thread.
        This method will block until the browser is successfully launched or fails.

        Args:
            (Same as your original class)
        """
        self._log = also_print(log_callback)
        self.timeout_ms = timeout_s * 1000  # Playwright timeout is in ms

        # --- Store config for the worker thread ---
        self.stealth_mode = stealth
        self.pause_browser = pause_browser
        self.render_page = render_page
        self.proxy_config: Optional[Dict[str, str]] = None

        # --- Queues for thread communication ---
        # Main thread -> Worker thread
        self.job_queue = queue.Queue()
        # Worker thread -> Main thread (for startup signal)
        self.startup_queue = queue.Queue(maxsize=1)

        # --- 1. Verify Library Availability ---
        if not sync_playwright:
            raise ImportError("Playwright is not installed.")
        if self.stealth_mode and (not sync_stealth and not Stealth):
            raise ImportError("Playwright-Stealth (v1 or v2) is not installed.")

        # --- 2. Parse Proxy Configuration ---
        if proxy:
            try:
                parsed_proxy = urlparse(proxy)
                if not all([parsed_proxy.scheme, parsed_proxy.hostname, parsed_proxy.port]):
                    raise ValueError("Proxy string must include scheme, host, and port.")
                self.proxy_config = {
                    "server": f"{parsed_proxy.scheme}://{parsed_proxy.hostname}:{parsed_proxy.port}"
                }
                if parsed_proxy.username: self.proxy_config["username"] = parsed_proxy.username
                if parsed_proxy.password: self.proxy_config["password"] = parsed_proxy.password
                self._log(f"Playwright proxy configured for server: {self.proxy_config['server']}")
            except Exception as e:
                self._log(f"!!! WARNING: Invalid proxy format '{proxy}'. Ignoring proxy. Error: {e}")
                self.proxy_config = None

        # --- 3. Start Worker Thread ---
        self._log("Starting Playwright worker thread...")
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

        # --- 4. Wait for Browser to Launch ---
        # This blocks __init__ until the worker is ready or fails
        try:
            # Wait up to 60s for browser to start
            startup_result = self.startup_queue.get(timeout=60)
            if isinstance(startup_result, Exception):
                raise startup_result  # Re-raise the exception from the worker thread
            self._log("Playwright worker thread started successfully.")
        except queue.Empty:
            self._log("[Fatal Error] Playwright worker thread timed out on startup.")
            raise TimeoutError("Playwright worker thread failed to start in time.")

    def _start_playwright(self):
        """[Worker Thread] Initializes Playwright and launches the browser."""
        mode = "Stealth" if self.stealth_mode else "Advanced"
        self._log(f"[Worker] Starting Playwright ({mode}, Slow)...")
        headless_mode = not self.pause_browser

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=headless_mode)

        log_msg = "[Worker] Headless browser started." if headless_mode \
            else "[Worker] Headful browser started (pause_browser=True)."
        self._log(log_msg)

    def _stop_playwright(self):
        """[Worker Thread] Shuts down the Playwright browser and process."""
        self._log("[Worker] Stopping Playwright browser...")
        if hasattr(self, 'browser') and self.browser:
            self.browser.close()
        if hasattr(self, 'playwright') and self.playwright:
            self.playwright.stop()
        self._log("[Worker] Playwright stopped.")

    def _worker_loop(self):
        """
        [Worker Thread] This is the main loop for the dedicated Playwright thread.
        It initializes Playwright and then waits for jobs.
        """
        try:
            self._start_playwright()
            self.startup_queue.put(True)  # Signal success to __init__
        except Exception as e:
            self._log(f"[Worker Error] Failed to start Playwright: {e}")
            self.startup_queue.put(e)  # Signal failure to __init__
            return  # Exit thread

        # --- Main Job Loop ---
        while True:
            try:
                # Wait for a job from the main thread
                # A job is a tuple: (url, result_queue)
                # Or a signal: ('shutdown', shutdown_complete_event)
                job_data = self.job_queue.get()
                if not job_data:
                    continue

                job_type, data, result_queue = job_data

                if job_type == 'shutdown':
                    self._log("[Worker] Shutdown signal received.")
                    result_queue.put(True)  # Acknowledge shutdown
                    break  # Exit loop

                if job_type == 'get_content':
                    url = data
                    self._log(f"[Worker] Starting job for: {url}")
                    try:
                        # Call the *actual* fetching logic
                        content = self._fetch_page_content(url)
                        result_queue.put(content)  # Send content back
                    except Exception as e:
                        self._log(f"[Worker Error] Job failed for {url}: {e}")
                        result_queue.put(e)  # Send exception back

            except Exception as e:
                self._log(f"[Worker Error] Unhandled error in worker loop: {e}")

        # --- Cleanup ---
        self._stop_playwright()
        self._log("[Worker] Thread exiting.")

    def get_content(self, url: str) -> Optional[bytes]:
        """
        [Main Thread] Fetches content from a URL.

        This method is synchronous and thread-safe. It sends the request
        to the background worker thread and blocks until the result is returned.
        """
        if not self.worker_thread.is_alive():
            raise RuntimeError(
                "Playwright worker thread is not running. Fetcher may have been closed or failed to start.")

        # Create a one-time queue to get the result back
        result_queue = queue.Queue(maxsize=1)

        # Send the job to the worker thread
        self.job_queue.put(('get_content', url, result_queue))

        # Block and wait for the result
        # Add a 10-second buffer to the timeout
        wait_timeout = (self.timeout_ms / 1000) + 10
        try:
            result = result_queue.get(timeout=wait_timeout)

            # If the worker sent back an exception, re-raise it in the main thread
            if isinstance(result, Exception):
                self._log(f"[Main Thread] Error received from worker for {url}")
                raise result

            return result
        except queue.Empty:
            self._log(f"[Main Thread] Timeout waiting for worker response for {url}")
            raise TimeoutError(f"Playwright job for {url} timed out after {wait_timeout}s")

    def _fetch_page_content(self, url: str) -> Optional[bytes]:
        """
        [Worker Thread] The *actual* browser logic,
        (This is your original get_content() method, renamed)
        """
        context = None
        try:
            context_options = {
                "user_agent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
            }
            if self.proxy_config:
                context_options["proxy"] = self.proxy_config

            context = self.browser.new_context(**context_options)
            page = context.new_page()

            if self.stealth_mode:
                if sync_stealth:
                    sync_stealth(page)
                elif Stealth:
                    Stealth().apply_stealth_sync(page)
                else:
                    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            else:
                page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            response = page.goto(url, timeout=self.timeout_ms, wait_until='domcontentloaded')

            if self.pause_browser:
                page.pause()

            if not response or not response.ok:
                status = response.status if response else 'N/A'
                # --- MODIFICATION: Raise error instead of returning None ---
                raise PlaywrightError(f"Failed to get valid response. Status: {status}")

            content_bytes: Optional[bytes]
            if self.render_page:
                content_str = page.content()
                content_bytes = content_str.encode('utf-8')
            else:
                content_bytes = response.body()

            context.close()
            return content_bytes

        except Exception as e:
            # --- MODIFICATION: Must raise exception to send it back to main thread ---
            self._log(f"[Worker Error] _fetch_page_content failed for {url}: {e}")
            if context:
                context.close()
            raise e  # Re-raise the exception

    def close(self):
        """
        [Main Thread] Shuts down the Playwright worker thread and browser.
        """
        self._log("Sending shutdown signal to worker thread...")
        if hasattr(self, 'worker_thread') and self.worker_thread.is_alive():
            try:
                # Use a queue to wait for acknowledgment
                shutdown_queue = queue.Queue(maxsize=1)
                self.job_queue.put(('shutdown', None, shutdown_queue))
                # Wait 10s for acknowledgment
                shutdown_queue.get(timeout=10)
            except queue.Empty:
                self._log("[Warning] Worker did not acknowledge shutdown signal.")

            # Wait for thread to fully exit
            self.worker_thread.join(timeout=10)
            if self.worker_thread.is_alive():
                self._log("[Error] Worker thread failed to join.")
        self._log("PlaywrightFetcher closed.")
