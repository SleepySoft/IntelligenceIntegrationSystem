"""
RSS Fetcher with Headless Browser & Proxy Support

Complete implementation with:
- Playwright headless browser
- HTTP/SOCKS5 proxy support
- Random user agents
- Comprehensive error handling
- Type annotations
"""
import logging
import feedparser
from bs4 import BeautifulSoup
from pydantic import BaseModel
from typing import Optional, Dict, List, Callable

from Scraper.ScraperBase import ScraperResult, ProxyConfig

logger = logging.getLogger(__name__)
context = None


DEFAULT_TIMEOUT_MS = 30000  # 30 seconds
MINIMAL_WAIT_SEC = 2


class RssMeta(BaseModel):
    title: str = ''                     # The title of channel (maybe)
    link: str = ''                      # Not the feed link. I have no idea.
    description: str = ''               # Description of this feed
    language: str = ''                  # Like: zh-cn
    updated: object | None = None       # ?


class RssItem(BaseModel):
    title: str                  # The title of article
    link: str                   # The link of article
    published: object | None    # Published time
    authors: list               # Authors but in most case it's empty
    description: str            # Description of this article
    guid: str                   # In most case it's empty
    categories: list            # In most case it's empty
    media: object | None        # ......


class FeedData(BaseModel):
    meta: RssMeta
    entries: List[RssItem]
    errors: List[str]
    fatal: bool


def parse_feed(content: str) -> FeedData:
    """
    Parses RSS/Atom content and outputs it in a standardized format.

    :param content: The original RSS content string
    :return: A dictionary containing metadata and entries. In FeedData.
    """
    errors = []

    try:
        parsed = feedparser.parse(content)

        if parsed.get("bozo", 0) == 1:
            exception = parsed.get("bozo_exception", Exception("Unknown parsing error"))
            errors.append(str(exception))
            logger.error(f'Feed XLM parse fail: {str(exception)}')

        meta = RssMeta(
            title = parsed.feed.get("title", ""),
            link = parsed.feed.get("link", ""),
            description = parsed.feed.get("description", ""),
            language = parsed.feed.get("language", "zh-cn"),
            updated = parsed.feed.get("updated_parsed", None)
        )

        # Process article items
        entries = []
        for entry in parsed.entries:
            authors = []
            for author_data in entry.get("authors", []):
                if author := author_data.get("name", '').strip():
                    authors.append(author)
            item = RssItem(
                title = entry.get("title", "Untitled"),
                link = entry.get("link", ""),
                published = entry.get("published_parsed", entry.get("updated_parsed", None)),
                authors = authors,
                description = sanitize_html(entry.get("description", "")),
                guid = entry.get("id", ""),
                categories = entry.get("tags", []),
                media = extract_media(entry)
            )
            entries.append(item)

        feed_data = FeedData(
            meta = meta,
            entries = entries,
            errors = errors,
            fatal = False
        )
        return feed_data

    except Exception as e:
        error_text = f"Exception: {str(e)}"
        errors.append(error_text)
        logger.error(error_text, exc_info=True)

        feed_data = FeedData(
            meta = RssMeta(),
            entries = [],
            errors = errors,
            fatal = True
        )
        return feed_data


def sanitize_html(raw: str) -> str:
    """清理HTML标签并保留文本内容"""
    return BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)


def extract_media(entry) -> list:
    """提取多媒体资源"""
    media = []
    # 处理enclosure标签
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith(("image/", "video/", "audio/")):
            media.append({
                "url": enc["href"],
                "type": enc["type"],
                "length": enc.get("length", 0)
            })
    # 处理media_content扩展
    for mc in entry.get("media_content", []):
        try:
            media.append({
                "url": mc["url"],
                "type": mc.get("type", "unknown"),
                "width": mc.get("width", 0),
                "height": mc.get("height", 0)
            })
        except Exception as e:
            logger.warning(f"Extract media from rss feed fail: {str(e)}")
    return media


def fetch_feed(
    url: str,
    fetch_content: Callable[..., ScraperResult],
    proxy: Optional[Dict[str, str]] = None,
    headless: bool = True
) -> FeedData:
    """
    Main entry point for fetching and parsing feeds
    :param fetch_content: The fetch_content function of scraper to fetch feed.
    :param url: Feed URL
    :param proxy: Proxy configuration in request style:
        {
            "http": "socks5://user:password@proxy_host:port",
            "https": "socks5://user:password@proxy_host:port"
        }
    :param headless: Browser visibility mode.
    :return: Result dictionary with status details.
    """
    try:
        result = fetch_content(url, timeout_ms=DEFAULT_TIMEOUT_MS, proxy=proxy, headless=headless)
        if result['content']:
            parsed = parse_feed(result['content'])
            return parsed
        else:
            raise ValueError('Emtpy feed content')
    except Exception as e:
        logger.error(f'Feed fetch fail: {str(e)}', exc_info=True)

        feed_data = FeedData(
            meta=RssMeta(),
            entries=[],
            errors=[str(e)],
            fatal=True
        )
        return feed_data


# ----------------------------------------------------------------------------------------------------------------------

def main():
    # SOCKS5 Proxy Example
    proxy_config = {
        "server": "socks5://127.0.0.1:10808",
        "username": "",
        "password": ""
    }

    result = fetch_feed(
        "https://feeds.feedburner.com/zhihu-daily",
        proxy=None
    )

    if 'entries' in result:
        for index, entry in enumerate(result['entries']):
            print(f'Item {index} : ')
            for k in sorted(entry.keys()):
                print(f'    {k} : {entry[k]}')

    # The entry dict looks like:
    # {
    #     'authors':        ['...'],
    #     'categories':     ['...'],
    #     'description':    '...',
    #     'guid':           '...',
    #     'link':           '...',
    #     'media':          { '...': '...' },
    #     'published':      '...',
    #     'title':          '...',
    # }

    if 'errors' in result:
        print(f'Errors: {result["errors"]}')

if __name__ == "__main__":
    main()