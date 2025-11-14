#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Extractor Module:
Defines the IExtractor interface and provides multiple implementations
for extracting main content from HTML and converting it to Markdown/HTML.
"""
import os  # <-- ADDED: For file path operations
import re
import json
import traceback
import unicodedata
import html2text
import lxml.etree
import hashlib  # <-- ADDED: For unique filename generation
from urllib.parse import urljoin, urlparse  # <-- ADDED: For parsing URLs
from bs4 import BeautifulSoup, Tag
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Literal

# --- Library Import Checks ---
try:
    from readability import Document

    print("Success: Imported 'readability-lxml'. ReadabilityExtractors are available.")
except ImportError:
    Document = None
    print("!!! FAILED to import 'readability-lxml'. ReadabilityExtractors will NOT be available.")

try:
    import trafilatura

    print("Success: Imported 'trafilatura'. TrafilaturaExtractor is available.")
except ImportError:
    trafilatura = None
    print("!!! FAILED to import 'trafilatura'. TrafilaturaExtractor will NOT be available.")

try:
    from newspaper import Article

    print("Success: Imported 'newspaper'. Newspaper3kExtractor is available.")
except ImportError:
    Article = None
    print("!!! FAILED to import 'newspaper'. Newspaper3kExtractor will NOT be available.")

try:
    import requests  # <-- ADDED: For image downloading

    print("Success: Imported 'requests'. Image downloading is available.")
except ImportError:
    requests = None
    print("!!! FAILED to import 'requests'. Image downloading will NOT be available.")

# --- Pydantic model for a standardized extraction result ---
try:
    from pydantic import BaseModel, Field, computed_field
except ImportError:
    # Fallback definitions if pydantic is not available
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

    markdown_content 字段现在可以包含 Markdown 或 HTML。
    """
    markdown_content: str = Field(
        default="",
        description="The main content in Markdown or clean HTML format.",
        repr=False
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted metadata (e.g., title, author, date, images_are_local)."
    )
    error: Optional[str] = Field(
        default=None,
        description="An error message if extraction failed."
    )

    @computed_field(repr=True)
    @property
    def content_preview(self) -> str:
        """A truncated preview of the content for repr."""
        if not self.markdown_content:
            return "[No Content]"
        cleaned_content = self.markdown_content.replace('\n', ' ')
        if len(cleaned_content) > 100:
            return cleaned_content[:100] + "..."
        return cleaned_content

    @property
    def success(self) -> bool:
        """Returns True if the extraction was successful (no error)."""
        return self.error is None

    def __str__(self):
        """Provides a comprehensive, human-readable summary."""
        if not self.success:
            return f"[Extraction FAILED]\n└── Error: {self.error}"

        output = ["[Extraction SUCCESS]"]
        title = self.metadata.get('title', '[No Title Found]')
        output.append(f"├── Title: {title}")

        # 检查是否为 HTML
        content_type = self.metadata.get('content_type', 'Markdown')
        output.append(f"├── Type: {content_type}")

        # 检查图片是否本地化
        is_local = self.metadata.get('images_are_local', False)
        if is_local:
            output.append(f"├── Images: Localized ({self.metadata.get('image_dir', 'N/A')})")

        if self.markdown_content:
            preview_str = self.markdown_content.replace('\n', ' ').strip()
            if len(preview_str) > 70:
                preview_str = preview_str[:70] + "..."
            elif not preview_str:
                preview_str = "[Content is whitespace]"
            output.append(f"├── Content: \"{preview_str}\"")
        else:
            output.append(f"├── Content: [No Content]")

        # 附加元数据信息
        if self.metadata:
            try:
                # 排除 content_type, images_are_local 以避免冗余
                meta_to_show = {k: v for k, v in self.metadata.items()
                                if k not in ['content_type', 'images_are_local', 'image_dir']}
                meta_json = json.dumps(meta_to_show, indent=2, ensure_ascii=False, default=str)
                meta_lines = [f"│   {line}" for line in meta_json.splitlines()]
                output.append(f"└── Metadata:\n" + "\n".join(meta_lines))
            except Exception as e:
                output.append(f"└── Metadata: [Error serializing: {e}]")
        else:
            output.append("└── Metadata: [None]")

        return "\n".join(output)


NormalizationForm: Literal["NFC", "NFD", "NFKC", "NFKD"] = 'NFKC'


# =======================================================================
# == UNICODE SANITIZER UTILITY
# =======================================================================

def sanitize_unicode_string(
        text: str,
        max_length: int = 10240,
        normalize_form: NormalizationForm = 'NFKC',
        allow_emoji: bool = False
) -> Optional[str]:
    """Sanitizes and cleans input string."""
    if not text:
        return ""
    if len(text) > max_length:
        text = text[:max_length]
    try:
        normalized = unicodedata.normalize(normalize_form, text)
    except ValueError as e:
        raise ValueError(f"Invalid normalization form: {normalize_form}") from e

    danger_pattern = re.compile(
        r'['
        r'\u0000-\u001F\u007F-\u009F' +
        r'\u0300-\u036F' +
        r'\u200B-\u200D\u202A-\u202E' +
        r'\uFFF0-\uFFFF' +
        r'\u180B-\u180D' +
        r'[\uDB40-\uDBFF][\uDC00-\uDFFF]' +
        r']',
        flags=re.UNICODE
    )

    sanitized = danger_pattern.sub('', normalized)
    sanitized = re.sub(r'[\uFE00-\uFE0F]', '', sanitized)
    return sanitized.strip()


# =======================================================================
# == ABSTRACT BASE CLASS (Interface)
# =======================================================================

class IExtractor(ABC):
    """
    Abstract base class for a content extractor.
    (内容提取器的抽象基类)
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.log_messages: List[str] = []

    def _log(self, message: str, indent: int = 0):
        log_msg = f"{' ' * (indent * 4)}{message}"
        self.log_messages.append(log_msg)
        if self.verbose:
            print(log_msg)

    @abstractmethod
    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
        """
        Extracts the main content and metadata from raw HTML bytes.
        """
        pass


# =======================================================================
# == CONCRETE IMPLEMENTATIONS
# =======================================================================

class SimpleExtractor(IExtractor):
    """
    A dummy extractor that simulates successful content extraction.
    (一个模拟内容提取的提取器。)
    """

    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
        self._log(f"Starting extraction for URL: {url}", indent=1)

        title = "Markdown + 图片提取器演示"
        # 使用一个真实的占位符 URL 来模拟 Trafilatura 的输出，以便 PDF 生成器可以下载它
        mock_image_url = 'https://placehold.co/600x200/5c98d6/FFFFFF/png?text=Placeholder'

        markdown_content = (
            f"# {title}\n\n"
            "这是 `SimpleExtractor` 模拟输出的 Markdown 内容。\n\n"
            "![模拟占位图](https://placehold.co/600x200/5c98d6/FFFFFF/png?text=Placeholder)\n\n"
            "### 总结\n"
            "此输出为 Markdown (默认类型)。"
        )

        self._log("Extraction simulated successfully (Markdown).", indent=1)

        return ExtractionResult(
            markdown_content=sanitize_unicode_string(markdown_content),
            metadata={"title": title, "url": url, "author": "Gemini", "content_type": "Markdown"},
        )


# ... (ReadabilityExtractor, Newspaper3kExtractor, ReadabilityHtmlExtractor unchanged)

class ReadabilityExtractor(IExtractor):
    """
    Extractor implementation using 'readability-lxml' to Markdown.
    """

    def __init__(self, verbose: bool = True):
        super().__init__(verbose)
        self.converter = html2text.HTML2Text()
        self.converter.ignore_links = False
        self.converter.ignore_images = False
        self.converter.body_width = 0

    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
        self._log(f"Extracting with ReadabilityExtractor (Markdown) from {url}")
        if not Document:
            error_str = "[Error] readability-lxml library not found."
            self._log(error_str)
            return ExtractionResult(error=error_str)

        try:
            html_str = content.decode('utf-8', errors='ignore')
            doc = Document(html_str)
            main_content_html = doc.summary()
            metadata = {'title': doc.title(), "content_type": "Markdown"}
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
    """

    def __init__(self, verbose: bool = True):
        super().__init__(verbose)
        self.converter = html2text.HTML2Text()
        self.converter.ignore_links = False
        self.converter.ignore_images = False
        self.converter.body_width = 0

    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
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
                return ExtractionResult(error="Newspaper3k failed to find content.")

            main_content_html = lxml.etree.tostring(article.top_node, encoding='unicode')
            markdown = self.converter.handle(main_content_html)

            # Extract rich metadata
            metadata = {
                'title': article.title,
                'authors': article.authors,
                'publish_date': article.publish_date,
                'top_image': article.top_image,
                'movies': article.movies,
                'keywords': article.keywords,
                'summary': article.summary,
                "content_type": "Markdown"
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


class ReadabilityHtmlExtractor(IExtractor):
    """
    Extractor implementation using 'readability-lxml' to output clean HTML.
    (使用 'readability-lxml' 提取器，直接输出干净的 HTML 片段，跳过 Markdown 转换。)
    """

    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
        self._log(f"Extracting with ReadabilityHtmlExtractor (HTML Only) from {url}")
        if not Document:
            error_str = "[Error] readability-lxml library not found."
            self._log(error_str)
            return ExtractionResult(error=error_str)

        try:
            html_str = content.decode('utf-8', errors='ignore')
            doc = Document(html_str)
            # doc.summary() 返回的就是干净的文章 HTML 片段
            main_content_html = doc.summary()

            metadata = {
                'title': doc.title(),
                "url": url,
                "content_type": "HTML"  # 明确标记为 HTML
            }

            # 注意：我们将 HTML 存储在 markdown_content 字段中，但通过 metadata 标记其类型
            return ExtractionResult(
                markdown_content=sanitize_unicode_string(main_content_html),
                metadata=metadata
            )
        except Exception as e:
            error_str = f"Readability HTML extraction failed: {e}"
            self._log(f"[Error] {error_str}")
            self._log(traceback.format_exc())
            return ExtractionResult(error=error_str)


# =======================================================================
# == NEW TRAFILATURA EXTRACTOR WITH IMAGE DOWNLOAD
# =======================================================================

class TrafilaturaExtractor(IExtractor):
    """
    Extractor implementation using 'trafilatura' to Markdown (better image support),
    with an option to download and localize images.
    (使用 'trafilatura' 提取器，侧重保留图片，可选择下载图片并将其本地化。)
    """

    # 正则表达式用于匹配 Markdown 图片链接: ![alt](url)
    IMAGE_PATTERN = re.compile(r'!\[(.*?)\]\((.*?)\)')

    def _download_and_rewrite_images(self, markdown_content: str, base_url: str, image_dir: str):
        """
        Downloads images referenced in markdown and rewrites URLs to local paths.
        (下载 Markdown 中引用的图片，并将 URL 重写为本地路径。)
        """
        if not requests:
            self._log("[Error] 'requests' not installed. Cannot download images.", 2)
            return markdown_content, False

        try:
            # 确保图片保存目录存在
            os.makedirs(image_dir, exist_ok=True)
            self._log(f"Image directory created/checked: {image_dir}", 2)
        except OSError as e:
            self._log(f"[Error] Could not create image directory {image_dir}: {e}", 2)
            return markdown_content, False

        download_success = False

        def image_replacer(match):
            nonlocal download_success
            alt_text = match.group(1)
            original_url = match.group(2)

            # 1. 解析绝对 URL
            absolute_url = urljoin(base_url, original_url)

            # 2. 生成唯一文件名
            url_path = urlparse(absolute_url).path
            extension = os.path.splitext(url_path)[1].lower()
            if not extension or len(extension) > 5 or '.' not in extension:
                # 尝试从响应头或 URL 参数中获取更准确的 MIME 类型，这里简化为默认 .jpg
                extension = '.jpg'

                # 使用 URL 的 SHA256 哈希值作为唯一文件名
            url_hash = hashlib.sha256(absolute_url.encode('utf-8')).hexdigest()[:10]
            filename = f"{url_hash}{extension}"
            local_path = os.path.join(image_dir, filename)

            # 3. 下载图片
            try:
                self._log(f"Downloading: {absolute_url}", 3)

                # 如果文件已存在，则跳过下载 (简单的缓存机制)
                if os.path.exists(local_path):
                    self._log(f"File already exists: {local_path}. Skipping download.", 3)
                else:
                    response = requests.get(absolute_url, stream=True, timeout=15)
                    response.raise_for_status()  # 检查 HTTP 状态码

                    with open(local_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)

                self._log(f"Successfully saved to: {local_path}", 3)
                download_success = True

                # 4. 重写 Markdown 引用：使用相对于当前工作目录的路径
                return f"![{alt_text}]({os.path.join(image_dir, filename)})"

            except Exception as e:
                self._log(f"[Error] Failed to download image from {absolute_url}: {e}", 3)
                # 下载失败，返回原始 URL 引用 (WeasyPrint 会尝试再次下载)
                return match.group(0)

                # 使用正则表达式替换函数处理所有图片链接

        rewritten_markdown = self.IMAGE_PATTERN.sub(image_replacer, markdown_content)

        return rewritten_markdown, download_success

    def extract(self, content: bytes, url: str,
                download_images: bool = False,
                image_dir: str = 'downloaded_images',
                **kwargs) -> ExtractionResult:

        self._log(f"Extracting with TrafilaturaExtractor (Markdown) from {url}")
        if not trafilatura:
            error_str = "[Error] trafilatura library not found."
            self._log(error_str)
            return ExtractionResult(error=error_str)

        try:
            html_str = content.decode('utf-8', errors='ignore')
            # 1. 提取 Markdown 内容 (包含原始图片 URL)
            markdown_content = trafilatura.extract(
                html_str,
                url=url,
                output_format='markdown',
                include_links=True,
                include_images=True,
            ) or ""  # 确保非 None

            # 2. 元数据准备
            soup = BeautifulSoup(html_str, 'html.parser')
            title = soup.find('title').get_text(strip=True) if soup.find('title') else 'Untitled'

            metadata = {
                "title": title,
                "url": url,
                "content_type": "Markdown",
                "images_are_local": False,  # 默认非本地
                "image_dir": image_dir  # 保存目录信息
            }

            # 3. 处理本地图片下载和路径重写
            if download_images:
                self._log("Image downloading requested. Starting download...", 1)

                # 执行下载和路径重写
                markdown_content_rewritten, success = self._download_and_rewrite_images(
                    markdown_content, url, image_dir
                )

                # 更新元数据旗标
                if success:
                    metadata["images_are_local"] = True

                markdown_content = markdown_content_rewritten

            return ExtractionResult(
                markdown_content=sanitize_unicode_string(markdown_content),
                metadata=metadata
            )
        except Exception as e:
            error_str = f"Trafilatura failed: {e}"
            self._log(f"[Error] {error_str}")
            self._log(traceback.format_exc())
            return ExtractionResult(error=error_str)