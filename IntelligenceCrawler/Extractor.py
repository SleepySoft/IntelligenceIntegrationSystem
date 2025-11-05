#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Extractor Module:
Defines the IExtractor interface and provides multiple implementations
for extracting main content from HTML and converting it to Markdown.
"""
import json
import re
import copy
import unicodedata
import traceback
import html2text
from abc import ABC, abstractmethod
from typing import Set, List, Dict, Any, Optional, Literal, TypeAlias
from bs4 import BeautifulSoup
import lxml.etree

# --- Library Import Checks ---
# These imports are optional. Implementations will check if they
# were successful before attempting to run.

try:
    from readability import Document

    print("Success: Imported 'readability-lxml'. ReadabilityExtractor is available.")
except ImportError:
    Document = None
    print("!!! FAILED to import 'readability-lxml'. ReadabilityExtractor will NOT be available.")
    print("!!! Please install it: pip install readability-lxml")

try:
    import trafilatura

    print("Success: Imported 'trafilatura'. TrafilaturaExtractor is available.")
except ImportError:
    trafilatura = None
    print("!!! FAILED to import 'trafilatura'. TrafilaturaExtractor will NOT be available.")
    print("!!! Please install it: pip install trafilatura")

try:
    from newspaper import Article

    print("Success: Imported 'newspaper'. Newspaper3kExtractor is available.")
except ImportError:
    Article = None
    print("!!! FAILED to import 'newspaper'. Newspaper3kExtractor will NOT be available.")
    print("!!! Please install it: pip install newspaper3k")

try:
    from crawl4ai.crawler import Crawler
    from crawl4ai.extraction import SmartExtractor

    print("Success: Imported 'crawl4ai'. Crawl4AIExtractor is available.")
except ImportError:
    Crawler = None
    SmartExtractor = None
    print("!!! FAILED to import 'crawl4ai'. Crawl4AIExtractor will NOT be available.")
    print("!!! Please install it: pip install crawl4ai")

# --- Type Alias for Unicode Sanitizer ---
try:
    from typing import TypeAlias
except ImportError:
    from typing_extensions import TypeAlias
# --- NEW: Pydantic model for a standardized extraction result ---
try:
    from pydantic import BaseModel, Field

    print("Success: Imported 'pydantic'. IExtractor will use ExtractionResult.")
except ImportError:
    print("!!! FAILED to import 'pydantic'. ExtractionResult will be a dict.")
    print("!!! Please install it: pip install pydantic")


    # Define fallback classes if pydantic isn't available
    # This allows the app to still run, albeit without type validation
    class BaseModel:
        def json(self, **kwargs):
            import json
            return json.dumps(self.__dict__, **kwargs)


    def Field(default=None, **kwargs):
        return default

class ExtractionResult(BaseModel):
    """
    Standardized return object for all IExtractor implementations.
    (所有 IExtractor 实现的标准返回对象。)
    """
    markdown_content: str = Field(default="", description="The main content in Markdown format.")
    metadata: Dict[str, Any] = Field(default_factory=dict,
                                     description="Extracted metadata (e.g., title, author, date).")
    error: Optional[str] = Field(default=None, description="An error message if extraction failed.")

    def __str__(self):
        """Helper for printing metadata."""
        import json
        return json.dumps(self.metadata, indent=2, ensure_ascii=False, default=str)

NormalizationForm: TypeAlias = Literal["NFC", "NFD", "NFKC", "NFKD"]


# =======================================================================
# == 1. UNICODE SANITIZER UTILITY (Your Code)
# =======================================================================

def sanitize_unicode_string(
        text: str,
        max_length: int = 10240,
        normalize_form: NormalizationForm = 'NFKC',
        allow_emoji: bool = False
) -> Optional[str]:
    """
    Sanitizes and cleans input string by removing Unicode variation selectors,
    combining characters, and other potentially dangerous Unicode features.
    (This function is from your provided code, with minor type hint fixes.)

    Args:
        text: Input string to be sanitized
        max_length: Maximum allowed input length (defense against bomb attacks)
        normalize_form: Unicode normalization form (NFKC recommended).
        allow_emoji: Whether to preserve emoji characters

    Returns:
        Sanitized string or None if input exceeds max_length
    """
    if not text:
        return ""

    # Defense against character bomb attacks
    if len(text) > max_length:
        text = text[:max_length]

    # Unicode normalization
    try:
        normalized = unicodedata.normalize(normalize_form, text)
    except ValueError as e:
        raise ValueError(f"Invalid normalization form: {normalize_form}") from e

    # Regex pattern for comprehensive filtering
    variation_selector_ranges = (
        r'\u180B-\u180D'  # Mongolian variation selectors
        r'\uFE00-\uFE0F'  # Unicode variation selectors
        r'[\uDB40-\uDBFF][\uDC00-\uDFFF]'  # Surrogate pairs handling
    )

    emoji_block = (r'\U0001F000-\U0001FAFF'  # Basic block (Note: \U for 32-bit)
                   r'\u231A-\u231B'  # Watch symbols
                   r'\u23E9-\u23FF'  # Control symbols
                   ) if not allow_emoji else ''

    danger_pattern = re.compile(
        r'['
        r'\u0000-\u001F\u007F-\u009F' +  # Control characters
        r'\u0300-\u036F' +  # Combining diacritics
        r'\u200B-\u200D\u202A-\u202E' +  # Zero-width/control characters
        r'\uFFF0-\uFFFF'  # Special purpose characters
        + emoji_block +
        variation_selector_ranges +
        r']',
        flags=re.UNICODE
    )

    sanitized = danger_pattern.sub('', normalized)
    sanitized = re.sub(r'[\uFE00-\uFE0F]', '', sanitized)  # Final check
    return sanitized.strip()


# =======================================================================
# == 2. ABSTRACT BASE CLASS (Interface)
# =======================================================================

class IExtractor(ABC):
    """
    Abstract base class for a content extractor.
    (内容提取器的抽象基类)

    The role of an extractor is to take raw HTML content and a URL,
    extract the main article content, and return it as a
    clean Markdown string.
    (提取器的职责是接收原始HTML内容和URL，
     提取主要文章内容，并将其作为干净的Markdown字符串返回。)
    """

    def __init__(self, verbose: bool = True):
        """
        Initializes the extractor.
        (初始化提取器。)

        :param verbose: Toggles detailed logging.
        """
        self.verbose = verbose
        self.log_messages: List[str] = []

    def _log(self, message: str, indent: int = 0):
        """
        Provides a unified logging mechanism.
        (提供统一的日志记录机制。)
        """
        log_msg = f"{' ' * (indent * 4)}{message}"
        self.log_messages.append(log_msg)
        if self.verbose:
            print(log_msg)

    @abstractmethod
    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
        """
        Extracts the main content and metadata from raw HTML bytes.
        (从原始HTML字节中提取主要内容和元数据。)

        :param content: The raw HTML content as bytes.
                        (作为字节的原始HTML内容。)
        :param url: The original URL (for context and resolving relative links).
                    (原始URL（用于上下文和解析相对链接）。)
        :param kwargs: Implementation-specific options (e.g., CSS selectors).
                       (特定于实现的选项（例如CSS选择器）。)
        :return: An ExtractionResult object containing markdown, metadata, and errors.
                 (一个包含Markdown、元数据和错误的 ExtractionResult 对象。)
        """
        pass


# =======================================================================
# == 3. IMPLEMENTATIONS
# =======================================================================

class TrafilaturaExtractor(IExtractor):
    """
    Extractor implementation using the 'trafilatura' library.
    (使用 'trafilatura' 库的提取器实现。)

    This is a highly effective algorithmic extractor that natively
    supports Markdown output.
    (这是一个高效的算法提取器，原生支持Markdown输出。)
    """

    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
        """
        Extracts content and metadata *separately* using trafilatura 2.0.0.
        Calls extract() twice to ensure clean separation.

        :param content: Raw HTML bytes.
        :param url: The original URL.
        :param kwargs: Passed to `trafilatura.extract()`.
        :return: ExtractionResult
        """
        self._log(f"Extracting with TrafilaturaExtractor from {url}")
        if not trafilatura:
            error_str = "[Error] Trafilatura library not found."
            self._log(error_str)
            return ExtractionResult(error=error_str)

        try:
            # --- 1. 第一次调用：只获取干净的 Markdown 内容 ---
            kwargs_content = kwargs.copy()
            kwargs_content.pop('output_format', None)
            kwargs_content.pop('with_metadata', None)  # 确保不请求元数据

            markdown = trafilatura.extract(
                content,
                url=url,
                output_format='markdown',  # 显式获取 Markdown
                include_links=True,  # 只保留内容相关的参数
                **kwargs_content
            )

            # --- 2. 第二次调用：只获取元数据 (通过 JSON) ---
            kwargs_meta = kwargs.copy()
            # 清除所有内容相关的参数
            kwargs_meta.pop('output_format', None)
            kwargs_meta.pop('include_links', None)
            kwargs_meta.pop('include_tables', None)

            json_string = trafilatura.extract(
                content,
                url=url,
                output_format='json',  # 请求 JSON
                with_metadata=True,  # <--- 使用正确的参数请求元数据
                **kwargs_meta
            )

            # --- 3. 解析 JSON 字符串以提取元数据 ---
            metadata = {}
            if json_string:
                try:
                    data_dict = json.loads(json_string)
                    # JSON 输出会把元数据和 'text' 放在同一个字典里
                    # 我们只提取元数据，忽略 'text'
                    metadata = {
                        'title': data_dict.get('title'),
                        'author': data_dict.get('author'),
                        'date': data_dict.get('date'),
                        'sitename': data_dict.get('sitename'),
                        'tags': data_dict.get('tags'),
                        'fingerprint': data_dict.get('fingerprint'),
                        'id': data_dict.get('id'),
                        'license': data_dict.get('license'),
                        'description': data_dict.get('description'),
                        'image': data_dict.get('image'),
                        'url': data_dict.get('url'),
                        'hostname': data_dict.get('hostname'),
                    }
                    # 移除值为 None 的键，保持 metadata 字典干净
                    metadata = {k: v for k, v in metadata.items() if v is not None}

                except json.JSONDecodeError as json_err:
                    self._log(f"[Warning] Trafilatura JSON output was invalid: {json_err}")
                except Exception as e:
                    self._log(f"[Warning] Failed to parse metadata JSON: {e}")

            if markdown is None and not metadata:
                self._log("[Info] Trafilatura returned None for both content and metadata.")
                return ExtractionResult(error="Trafilatura failed to extract content.")

            return ExtractionResult(
                markdown_content=sanitize_unicode_string(markdown or ""),
                metadata=metadata  # 这是干净的 metadata 字典
            )

        except Exception as e:
            error_str = f"Trafilatura failed: {e}"
            self._log(f"[Error] {error_str}")
            self._log(traceback.format_exc())
            return ExtractionResult(error=error_str)


class ReadabilityExtractor(IExtractor):
    """
    Extractor implementation using 'readability-lxml'.
    (使用 'readability-lxml' 的提取器实现。)

    This is a refactored version of your `clean_html_content` function.
    It uses the Readability algorithm to find the main content HTML,
    then converts that HTML to Markdown.
    (这是您 `clean_html_content` 函数的重构版本。
     它使用Readability算法找到主要内容HTML，然后将其转换为Markdown。)
    """

    def __init__(self, verbose: bool = True):
        super().__init__(verbose)
        self.converter = html2text.HTML2Text()
        self.converter.ignore_links = False
        self.converter.ignore_images = False  # Markdown should include images
        self.converter.body_width = 0  # Don't wrap lines

    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
        """
        Extracts content using readability-lxml.

        :param content: Raw HTML bytes.
        :param url: The original URL (ignored, but part of interface).
        :param kwargs: Ignored by this implementation.
        :return: Markdown string.
        """
        self._log(f"Extracting with ReadabilityExtractor from {url}")
        if not Document:
            error_str = "[Error] readability-lxml library not found."
            self._log(error_str)
            return ExtractionResult(error=error_str)

        try:
            html_str = content.decode('utf-8', errors='ignore')
            doc = Document(html_str)
            main_content_html = doc.summary()

            # Try to get the title from the Document object
            metadata = {'title': doc.title()}

            markdown = self.converter.handle(main_content_html)
            return ExtractionResult(
                markdown_content=sanitize_unicode_string(markdown),
                metadata=metadata
            )
        except Exception as e:
            error_str = f"Readability failed: {e}"
            self._log(f"[Error] {error_str}")
            self._log(traceback.format_exc())
            return ExtractionResult(error=error_str)


class Newspaper3kExtractor(IExtractor):
    """
    Extractor implementation using the 'newspaper3k' library.
    (使用 'newspaper3k' 库的提取器实现。)

    Newspaper3k only extracts plain text. This implementation
    works around this by:
    1. Letting newspaper3k parse the HTML.
    2. Grabbing the 'top_node' (the lxml element it found).
    3. Converting that element back to HTML.
    4. Converting the resulting HTML to Markdown.
    (Newspaper3k 只能提取纯文本。此实现通过以下方式解决：
     1. 让 newspaper3k 解析HTML。
     2. 获取 'top_node' (它找到的 lxml 元素)。
     3. 将该元素转换回HTML。
     4. 将生成的HTML转换为Markdown。)
    """

    def __init__(self, verbose: bool = True):
        super().__init__(verbose)
        self.converter = html2text.HTML2Text()
        self.converter.ignore_links = False
        self.converter.ignore_images = False
        self.converter.body_width = 0

    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
        """
        Extracts content using newspaper3k.

        :param content: Raw HTML bytes.
        :param url: The original URL (required by newspaper).
        :param kwargs: Ignored by this implementation.
        :return: Markdown string.
        """
        self._log(f"Extracting with Newspaper3kExtractor from {url}")
        if not Article:
            error_str = "[Error] newspaper3k library not found."
            self._log(error_str)
            return ExtractionResult(error=error_str)

        try:
            html_str = content.decode('utf-8', errors='ignore')
            article = Article(url)
            article.set_html(html_str)
            article.parse()

            if article.top_node is None:
                self._log("[Info] Newspaper3k could not find a top_node.")
                return ""  # Failed to find content

            # Convert the main lxml node back to HTML
            if article.top_node is None:
                self._log("[Info] Newspaper3k could not find a top_node.")
                return ExtractionResult(error="Newspaper3k failed to find content.")

                # Convert the main lxml node back to HTML
            main_content_html = lxml.etree.tostring(article.top_node, encoding='unicode')
            markdown = self.converter.handle(main_content_html)

            # --- MODIFICATION: Extract rich metadata ---
            metadata = {
                'title': article.title,
                'authors': article.authors,
                'publish_date': article.publish_date,
                'top_image': article.top_image,
                'movies': article.movies,
                'keywords': article.keywords,
                'summary': article.summary,
            }

            return ExtractionResult(
                markdown_content=sanitize_unicode_string(markdown),
                metadata=metadata
            )
        except Exception as e:
            error_str = f"Newspaper3k failed: {e}"
            self._log(f"[Error] {error_str}")
            self._log(traceback.format_exc())
            return ExtractionResult(error=error_str)


class GenericCSSExtractor(IExtractor):
    """
    Extractor implementation based on user-provided CSS selectors.
    (基于用户提供的CSS选择器的提取器实现。)

    This is a refactored version of your `html_content_converter` function.
    It requires 'selectors' to be passed in the `kwargs`.
    (这是您 `html_content_converter` 函数的重构版本。
     它要求在 `kwargs` 中传入 'selectors'。)
    """

    def __init__(self, verbose: bool = True):
        super().__init__(verbose)
        self.converter = html2text.HTML2Text()
        self.converter.ignore_links = False
        self.converter.ignore_images = False
        self.converter.body_width = 0

    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
        """
        Extracts content using specific CSS selectors.

        :param content: Raw HTML bytes.
        :param url: The original URL (ignored).
        :param kwargs: Must contain:
                       - 'selectors' (str or List[str]): CSS selector(s) for target content.
                       - 'exclude_selectors' (Optional[List[str]]): CSS selector(s) to remove.
        :return: Markdown string.
        """
        self._log(f"Extracting with GenericCSSExtractor from {url}")

        # --- Get required arguments from kwargs ---
        selectors = kwargs.get('selectors')
        if isinstance(selectors, str):
            selectors = [selectors]
        elif not selectors:
            error_str = "GenericCSSExtractor requires 'selectors' argument in kwargs."
            self._log(f"[Error] {error_str}")
            return ExtractionResult(error=error_str)

        exclude_selectors = kwargs.get('exclude_selectors', [])
        if isinstance(exclude_selectors, str):
            exclude_selectors = [exclude_selectors]

        # --- This is your logic from html_content_converter ---
        try:
            html_str = content.decode('utf-8', errors='ignore')
            soup = BeautifulSoup(html_str, 'html.parser')

            extracted_elements = []
            for selector in selectors:
                elements = soup.select(selector)
                for element in elements:
                    element_copy = copy.copy(element)  # Work on a copy

                    # Remove excluded elements
                    for ex_selector in exclude_selectors:
                        for unwanted in element_copy.select(ex_selector):
                            unwanted.decompose()

                    extracted_elements.append(element_copy)

            if not extracted_elements:
                self._log("[Info] No elements found for the given selectors.")
                return ""

            # Convert all found elements to Markdown and join them
            markdown_parts = [
                self.converter.handle(str(el)).strip()
                for el in extracted_elements
            ]
            full_markdown = '\n\n'.join(markdown_parts)

            return ExtractionResult(
                markdown_content=sanitize_unicode_string(full_markdown),
                metadata={'source': 'Generic CSS Selector'}
            )

        except Exception as e:
            error_str = f"GenericCSSExtractor failed: {e}"
            self._log(f"[Error] {error_str}")
            return ExtractionResult(error=error_str)


class Crawl4AIExtractor(IExtractor):
    """
    Extractor implementation using the 'crawl4ai' library.
    (使用 'crawl4ai' 库的提取器实现。)

    WARNING: This extractor IGNORES the pre-fetched `content`
    because crawl4ai must run its own browser instance to
    analyze the page for AI extraction. It will RE-FETCH the `url`.
    (警告：此提取器会忽略预先获取的 `content`，
     因为 crawl4ai 必须运行自己的浏览器实例来分析页面以进行AI提取。
     它将重新抓取 `url`。)
    """

    def __init__(self, model_name: str = 'gpt-3.5-turbo', verbose: bool = True):
        """
        :param model_name: The AI model to use (e.g., 'gpt-3.5-turbo', 'gpt-4o').
        """
        super().__init__(verbose)
        self.model_name = model_name

    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
        """
        Extracts content using crawl4ai's SmartExtractor.

        :param content: IGNORED.
        :param url: The URL to crawl and extract from.
        :param kwargs: Passed to `SmartExtractor`.
                       Example: `extraction_prompt="Extract only user comments"`
        :return: Markdown string.
        """
        self._log(f"Extracting with Crawl4AIExtractor from {url}")
        if not Crawler or not SmartExtractor:
            error_str = "crawl4ai library not found."
            self._log(f"[Error] {error_str}")
            return ExtractionResult(error=error_str)

        self._log("[Warning] Crawl4AIExtractor ignores pre-fetched content and is re-fetching the URL.")

        try:
            extractor = SmartExtractor(
                model=self.model_name,
                **kwargs
            )

            crawler = Crawler(extractor=extractor)
            result = crawler.run(url)

            if result and (result.markdown or result.structured_data):
                # crawl4ai returns markdown AND structured_data (which is our metadata)
                metadata = result.structured_data or {}
                metadata['source'] = 'Crawl4AI'

                return ExtractionResult(
                    markdown_content=sanitize_unicode_string(result.markdown),
                    metadata=metadata
                )

            self._log("[Info] Crawl4AI ran but returned no markdown or data.")
            return ExtractionResult(error="Crawl4AI returned no content.")
        except Exception as e:
            error_str = f"Crawl4AIExtractor failed: {e}"
            self._log(f"[Error] {error_str}")
            self._log(traceback.format_exc())
            return ExtractionResult(error=error_str)
