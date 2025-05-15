import traceback
from typing import Optional, Dict, Any
from Scraper.PlaywrightRawScraper import request_by_browser


def fetch_content(
    url: str,
    timeout_ms: int,
    proxy: Optional[Dict[str, str]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    The same as base.
    :param url: The same as base.
    :param timeout_ms: The same as base.
    :param proxy: Format: The same as base.
    :return: The same as base.
    """

    try:
        def handler(page, response):
            if not response:
                return {'content': '', "errors": 'No response'}
            if response.status >= 400:
                return {'content': '', "errors": f'HTTP response: {response.status}'}

            try:
                # page.wait_for_load_state('load', timeout=self.timeout)
                page.wait_for_load_state('domcontentloaded', timeout=timeout_ms)
                # page.wait_for_load_state('networkidle', timeout=self.timeout)
            except Exception as e:
                print(f'Rendered scraper gets error: {str(e)}')
            finally:
                page_content = page.content()
                return {'content': page_content, "errors": []}

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
