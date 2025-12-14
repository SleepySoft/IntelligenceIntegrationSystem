import traceback
from typing import Optional
from Scraper.ScraperBase import ScraperResult, ProxyConfig
from Scraper.PlaywrightRawScraper import request_by_browser


def fetch_content(
    url: str,
    timeout_ms: int,
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

    def handler(page, response):
        if not response:
            return {'content': '', "errors": ['No response']}

        if response.status >= 400:
            return {'content': '', "errors": [f'HTTP response: {response.status}']}

        error_msgs = []

        try:
            page.wait_for_load_state('domcontentloaded', timeout=timeout_ms)
        except Exception as e:
            err_str = f"Wait for load state failed: {str(e)}"
            print(err_str)
            error_msgs.append(err_str)

        try:
            page_content = page.content()
        except Exception as e:
            err_str = f"Failed to get page content: {str(e)}"
            print(err_str)
            error_msgs.append(err_str)
            return {'content': '', "errors": error_msgs}

        return {'content': page_content, "errors": error_msgs}

    try:
        result = request_by_browser(url, handler, timeout_ms, proxy)
        return result
    except Exception as e:
        print(traceback.format_exc())
        return {'content': '', "errors": [str(e)]}


# ----------------------------------------------------------------------------------------------------------------------

def main():
    result = fetch_content("https://machinelearningmastery.com/further-applications-with-context-vectors/",
                           timeout_ms=20000)
    html = result['content']
    if html:
        with open('../web.html', 'wt', encoding='utf-8') as f:
            f.write(html)


# Usage example
if __name__ == "__main__":
    main()
