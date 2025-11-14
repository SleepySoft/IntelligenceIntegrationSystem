import os
import io
from urllib.parse import urljoin
from pathlib import Path  # <-- ADDED: For cleaner path handling

# Dependencies from extractor.py
from extractor import IExtractor, SimpleExtractor, ExtractionResult, ReadabilityHtmlExtractor, TrafilaturaExtractor

# Third-party libraries for PDF generation and HTML manipulation
try:
    import weasyprint
    import markdown
    from bs4 import BeautifulSoup
except ImportError:
    print("!!! Please install required libraries: pip install weasyprint markdown beautifulsoup4")
    # Exit or raise error if dependencies are critical
    weasyprint = None
    markdown = None
    BeautifulSoup = None


# =======================================================================
# == HELPER: IMAGE RESOLUTION
# =======================================================================

def _resolve_relative_images(html_content: str, base_url: str) -> str:
    """
    Uses BeautifulSoup to find all <img> tags and converts their
    relative 'src' attributes into absolute URLs using the base_url.

    NOTE: This is only used when images are NOT downloaded locally.

    :param html_content: The HTML string (after MD conversion or direct HTML).
    :param base_url: The original URL of the webpage.
    :return: HTML string with all image source URLs resolved to absolute paths.
    """
    if not BeautifulSoup:
        return html_content  # Cannot resolve without BeautifulSoup

    soup = BeautifulSoup(html_content, 'html.parser')

    # 查找所有图片标签
    for img in soup.find_all('img'):
        src = img.get('src')
        if src:
            # 使用 urljoin 解决相对路径
            absolute_src = urljoin(base_url, src)

            # WeasyPrint 需要绝对路径才能在渲染时下载图片
            if absolute_src != src:
                img['src'] = absolute_src
                print(f"  -> Resolved relative image: {src} -> {absolute_src}")

    return str(soup)


# =======================================================================
# == PDF GENERATOR CLASS
# =======================================================================

class PDFGenerator:
    """
    A class to extract web content using an IExtractor and save it as a PDF.
    (一个使用 IExtractor 提取网页内容并将其保存为 PDF 的类。)
    """

    def __init__(self, extractor: IExtractor):
        """
        :param extractor: An instance of a class derived from IExtractor.
        """
        if not weasyprint or not markdown:
            raise RuntimeError("PDF generation dependencies (weasyprint, markdown) are not installed.")
        self.extractor = extractor
        print(f"PDFGenerator initialized with extractor: {extractor.__class__.__name__}")

    def generate_pdf(self,
                     raw_html_bytes: bytes,
                     source_url: str,
                     output_path: str,
                     download_images: bool = False,  # <-- NEW PARAMETER
                     image_dir: str = 'downloaded_images'  # <-- NEW PARAMETER
                     ) -> Optional[str]:
        """
        Extracts content, resolves image links, and generates the PDF file.
        """
        print(f"\n--- Starting PDF Generation: {output_path} ---")

        # 1. 使用 IExtractor 提取内容，并传递图片下载选项
        # TrafilaturaExtractor 现在能够处理这些 kwargs
        result = self.extractor.extract(
            raw_html_bytes,
            source_url,
            download_images=download_images,
            image_dir=image_dir
        )

        if not result.success:
            print(f"!!! Extraction failed: {result.error}")
            return result.error

        content_type = result.metadata.get('content_type', 'Markdown')
        content_str = result.markdown_content
        images_are_local = result.metadata.get('images_are_local', False)

        # 2. 从内容转换为基础 HTML
        if content_type == 'HTML':
            print("  -> Detected HTML content. Using directly.")
            html_for_body = content_str
        else:
            print("  -> Detected Markdown content. Converting to HTML.")
            html_for_body = markdown.markdown(content_str)

        # 3. 嵌入标准模板
        html_from_template = self._standard_html_generator(
            content_html=html_for_body,
            title=result.metadata.get('title', 'Untitled Document')
        )

        # 4. 路径处理和 WeasyPrint base_url 决定
        pdf_base_url = source_url  # 默认使用远程 URL

        if images_are_local:
            print(f"  -> Local images detected. Setting WeasyPrint base_url to local path.")
            # 获取图片目录的绝对路径
            abs_image_dir = Path(image_dir).resolve()
            # 将本地路径转换为 file:// URL 格式，供 WeasyPrint 使用
            pdf_base_url = abs_image_dir.as_uri() + "/"

            # 由于路径已经是本地的，不需要再进行远程 URL 解析，只需确保 HTML 完整
            final_html_for_pdf = html_from_template
        else:
            print(f"  -> Remote images/No images. Resolving remote paths.")
            # 如果没有本地图片，则需要确保所有相对 URL 都被解析为绝对 URL
            final_html_for_pdf = _resolve_relative_images(html_from_template, source_url)

        # 5. 使用 WeasyPrint 生成 PDF
        try:
            # 使用决定好的 pdf_base_url
            html_doc = weasyprint.HTML(string=final_html_for_pdf, base_url=pdf_base_url)

            # 渲染并写入文件
            html_doc.write_pdf(output_path)

            print(f"*** PDF successfully generated at: {os.path.abspath(output_path)} ***")

            # 如果启用了本地下载，提醒用户图片目录
            if images_are_local:
                print(f"*** Associated images saved in: {abs_image_dir} ***")

            return output_path

        except Exception as e:
            print(f"!!! PDF Generation Error: {e}")
            return f"PDF Generation Error: {e}"

    def _standard_html_generator(self, content_html: str, title: str) -> str:
        """
        Wraps the extracted HTML content in a basic, printable HTML structure
        with some default styling.
        """
        html_template = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        @page {{
            size: A4;
            margin: 2cm;
        }}
        body {{
            font-family: 'Noto Sans', sans-serif;
            color: #333;
            line-height: 1.6;
        }}
        h1, h2, h3 {{
            border-bottom: 1px solid #eee;
            padding-bottom: 5px;
            color: #1a1a1a;
        }}
        img {{
            max-width: 100%;
            height: auto;
            border: 1px solid #ccc;
            padding: 5px;
            display: block;
            margin: 20px auto; /* 居中 */
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
        }}
        pre, code {{
            background-color: #f4f4f4;
            padding: 2px 4px;
            border-radius: 4px;
        }}
        pre {{
            padding: 10px;
            overflow-x: auto;
            border: 1px solid #ddd;
        }}
    </style>
</head>
<body>
    <header style="text-align: center; margin-bottom: 30px;">
        <h1 style="border-bottom: none;">{title}</h1>
        <p style="color: #666; font-style: italic;">Generated by PDFGenerator (Extractor: {{self.extractor.__class__.__name__}})</p>
    </header>
    <article>
        {content_html}
    </article>
</body>
</html>
"""
        return html_template.format(self=self)  # 使用 format 传入 self


# =======================================================================
# == DEMONSTRATION / USAGE
# =======================================================================

if __name__ == '__main__':
    # 模拟输入数据
    # Trafilatura 会尝试从这个 URL 获取元数据
    source_url = "https://www.example.com/some/article/path"
    # 模拟的原始 HTML 内容（Trafilatura 会尝试从其中提取）
    # 为了演示，我们给出一个包含图片链接的模拟 HTML 片段
    dummy_html_content = f"""
    <html>
        <head><title>测试本地图片下载</title></head>
        <body>
            <div id="content">
                <h1>测试文章标题</h1>
                <p>这是一段包含图片的测试文本。</p>
                <img src="/static/img/test1.jpg" alt="相对路径图片">
                <p>第二张图片将使用绝对路径。</p>
                <img src="https://placehold.co/300x150/ff9900/FFFFFF/png?text=Remote" alt="远程图片">
            </div>
        </body>
    </html>
    """.encode('utf-8')

    # ----------------------------------------------------
    # 演示 1: 使用 TrafilaturaExtractor，并启用本地图片下载
    # ----------------------------------------------------

    print("--- 演示 1: 使用 TrafilaturaExtractor (Markdown + 本地图片) ---")

    if TrafilaturaExtractor is not None and os.getenv('CI') != 'true':  # 避免在受限环境中尝试下载
        extractor_md = TrafilaturaExtractor(verbose=True)
        pdf_generator_md = PDFGenerator(extractor=extractor_md)
        output_pdf_md = "report_trafilatura_local.pdf"
        image_save_dir = "local_images_demo"

        try:
            # 启用图片下载，并指定目录
            result_path = pdf_generator_md.generate_pdf(
                raw_html_bytes=dummy_html_content,
                source_url=source_url,
                output_path=output_pdf_md,
                download_images=True,  # <--- 启用本地下载
                image_dir=image_save_dir
            )

            if result_path and not result_path.startswith("!!!"):
                print(f"\n✅ Success: 本地图片 PDF 生成于 '{output_pdf_md}'")
                print(f"✅ Images should be in directory: {image_save_dir}")
            else:
                print(f"\n❌ Failure during本地图片 PDF generation.")
        except RuntimeError as e:
            print(f"\n🚨 Fatal Error: {e}")

    print("\n" + "=" * 50 + "\n")

    # ----------------------------------------------------
    # 演示 2: 使用 ReadabilityHtmlExtractor (HTML 直出, 远程图片)
    # ----------------------------------------------------

    print("--- 演示 2: 使用 ReadabilityHtmlExtractor (HTML 直出, 远程图片) ---")

    if ReadabilityHtmlExtractor is not None:
        extractor_html = ReadabilityHtmlExtractor(verbose=True)
        pdf_generator_html = PDFGenerator(extractor=extractor_html)
        output_pdf_html = "report_readability_remote.pdf"

        try:
            # 不启用图片下载，依赖 WeasyPrint 实时下载远程图片
            result_path = pdf_generator_html.generate_pdf(
                raw_html_bytes=dummy_html_content,
                source_url=source_url,
                output_path=output_pdf_html,
                download_images=False  # <--- 不下载
            )
            if result_path and not result_path.startswith("!!!"):
                print(f"\n✅ Success: 远程图片 PDF 生成于 '{output_pdf_html}'")
            else:
                print(f"\n❌ Failure during 远程图片 PDF generation.")
        except RuntimeError as e:
            print(f"\n🚨 Fatal Error: {e}")

    # 提醒用户安装依赖
    if weasyprint is None:
        print("\n\n!!! 缺少依赖库 WeasyPrint/Markdown/BeautifulSoup4。请运行安装命令以运行 demo。")
    if requests is None:
        print("\n!!! 缺少 'requests' 库。演示 1 (本地图片下载) 将无法运行。请安装：pip install requests")