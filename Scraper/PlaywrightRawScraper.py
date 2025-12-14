"""
RSS Fetcher with Headless Browser & Proxy Support

Complete implementation with:
- Playwright headless browser
- HTTP/SOCKS5 proxy support
- Random user agents
- Comprehensive error handling
- Type annotations
"""
import random
import traceback
from urllib.parse import urlparse
from typing import Optional, Dict, Any, List
from playwright.sync_api import sync_playwright

from IntelligenceCrawler.BrowserMonitor import AutoTrackedBrowser
from Scraper.ScraperBase import ScraperResult, ProxyConfig
from Tools.ProxyFormatParser import to_playwright_format, parse_to_intermediate

DEFAULT_TIMEOUT_MS = 8000  # 8 seconds


DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


class BrowserManager:
    """Manage browser instance with proxy and anti-detection settings"""

    ANTI_DETECTION_ARGS = [
        '--disable-blink-features=AutomationControlled',
        '--disable-automation-software-banner',  # 隐藏自动化提示
        '--disable-dev-shm-usage',
        '--no-sandbox',
        '--enable-features=NetworkService,NetworkServiceInProcess',
        '--disable-site-isolation-for-enterprise-policy'
    ]

    def __init__(
        self,
        headless: bool = True,
        proxy: Optional[Dict[str, str]] = None,
        user_agents: Optional[List[str]] = None
    ):
        self.playwright = None
        self.browser = None

        if proxy:
            proxy = to_playwright_format(parse_to_intermediate(proxy))
        self.proxy = proxy

    def __enter__(self):
        try:
            self.open()
            return self.browser
        except Exception as e:
            self.close()
            raise RuntimeError(f"Browser launch fail: {str(e)}")

    def __exit__(self, *args):
        self.close()

    def open(self):
        if not self.playwright:
            self.playwright = sync_playwright().start()
            try:
                options = self._prepare_launch_options(headless=True)
                real_browser = self.playwright.chromium.launch(**options)
                self.browser: "Browser" = AutoTrackedBrowser(real_browser)
            except Exception as e:
                self.playwright.stop()
                raise

    def close(self):
        if self.browser:
            try:
                self.browser.close()
            except Exception as e:
                print(f"Browser close exception: {e}")
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception as e:
                print(f"Playwright close exception: {e}")

    def _prepare_launch_options(self, headless: bool) -> Dict[str, Any]:
        options = {
            "args": BrowserManager.ANTI_DETECTION_ARGS + [
                f'--window-size={random.randint(1000, 1400)},{random.randint(800, 1200)}',  # 随机窗口尺寸
                f'--lang=en-US',
            ],
            "headless": headless,
            "slow_mo": random.randint(100, 500),  # 模拟人类操作间隔
        }

        if self.proxy:
            self._validate_proxy_config()
            options["proxy"] = {
                "server": self.proxy["server"],
                "username": self.proxy.get("username"),
                "password": self.proxy.get("password")
            }

        return options

    def _validate_proxy_config(self):
        try:
            parsed = urlparse(self.proxy["server"])
            if not all([parsed.scheme, parsed.hostname, parsed.port]):
                raise ValueError
            if parsed.scheme not in ['http', 'https', 'socks5']:
                raise ValueError
        except:
            raise ValueError(f"Invalid proxy format: {self.proxy['server']}")


def request_by_browser(
    url: str,
    handler: callable,
    timeout: int = DEFAULT_TIMEOUT_MS,
    proxy: Optional[Dict[str, str]] = None,
    **kwargs
):
    """
    Request by headless browser. Handle request result by callable.
    :param url: The request url.
    :param handler:
    :param timeout:
    :param proxy:
    :return:
    """
    context_args = {
        "user_agent": random.choice(DEFAULT_USER_AGENTS),
        "viewport": {"width": 1366, "height": 768},
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "java_script_enabled": True,
        "ignore_https_errors": True,
        "extra_http_headers": {
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",  # 指定内容类型
        }
    }

    with BrowserManager(headless=True, proxy=proxy) as browser:
        with browser.new_context(**context_args) as context:
            with context.new_page() as page:
                try:
                    response = page.goto(url, timeout=timeout, wait_until="domcontentloaded")
                    return handler(page, response)
                except Exception as e:
                    print(f'request_by_browser gets exception: {str(e)}')
                    # print(traceback.format_exc())
                    return {'content': '', "errors": [str(e)]}


def fetch_content(
    url: str,
    timeout_ms: Optional[int] = DEFAULT_TIMEOUT_MS,
    proxy: Optional[ProxyConfig] = None,
    **kwargs
) -> ScraperResult:
    """
    The same as base.
    :param url: The same as base.
    :param timeout_ms: The same as base.
    :param proxy: Format: The same as base.
    :return: The same as base.
    """
    try:
        def handler(_, response):
            if not response:
                return {'content': '', "errors": 'No response'}
            if response.status >= 400:
                return {'content': '', "errors": f'HTTP response: {response.status}'}
            raw_content = response.text()
            return {'content': raw_content, "errors": ''}

        result = request_by_browser(url, handler, timeout_ms, proxy)
        return result
    except Exception as e:
        traceback.print_exc()
        return {'content': '', "errors": [str(e)]}


# ----------------------------------------------------------------------------------------------------------------------

def main():
    # SOCKS5 Proxy Example
    proxy_config = {
        "server": "socks5://127.0.0.1:10808",
        "username": "",
        "password": ""
    }

    result = fetch_content(
        "https://blogs.technet.microsoft.com/machinelearning/feed",
        proxy=None
    )

    print(result['content'])
    print(result['errors'])

if __name__ == "__main__":
    main()