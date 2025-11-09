import json
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Set
from collections import defaultdict
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field, computed_field


# --- Begin: 用户提供的基类 (User-Provided Base Classes) ---
# (我将您提供的类复制到这里，以便文件能独立运行)

class ExtractionResult(BaseModel):
    """
    Standardized return object for all IExtractor implementations.
    (所有 IExtractor 实现的标准返回对象。)
    """
    markdown_content: str = Field(
        default="",
        description="The main content in Markdown format.",
        repr=False  # 1. 不在 __repr__ 中显示完整内容，避免刷屏
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted metadata (e.g., title, author, date)."
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
        """
        Provides a comprehensive, human-readable summary.
        (提供一个全面且易读的摘要。)
        """
        if not self.success:
            return f"[Extraction FAILED]\n└── Error: {self.error}"

        output = ["[Extraction SUCCESS]"]
        title = self.metadata.get('title')
        if title:
            output.append(f"├── Title: {title}")
        else:
            output.append(f"├── Title: [No Title Found]")

        if self.markdown_content:
            preview_str = self.markdown_content.replace('\n', ' ').strip()
            if len(preview_str) > 70:
                preview_str = preview_str[:70] + "..."
            elif not preview_str:
                preview_str = "[Content is whitespace]"
            output.append(f"├── Content: \"{preview_str}\"")
        else:
            output.append(f"├── Content: [No Content]")

        if self.metadata:
            try:
                # 使用 default=str 来处理任何无法序列化的对象
                meta_json = json.dumps(self.metadata, indent=2, ensure_ascii=False, default=str)
                meta_lines = [f"│   {line}" for line in meta_json.splitlines()]
                output.append(f"└── Metadata:\n" + "\n".join(meta_lines))
            except Exception as e:
                output.append(f"└── Metadata: [Error serializing: {e}]")
        else:
            output.append("└── Metadata: [None]")

        return "\n".join(output)


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
        pass


# --- End: 用户提供的基类 ---


# --- Begin: 链接指纹和聚类的数据模型 ---

class LinkFingerprint(BaseModel):
    """
    Represents a single link and its structural context.
    (代表单个链接及其结构上下文。)
    """
    href: str = Field(description="完整的、绝对路径的URL")
    text: str = Field(description="链接的可见文本")
    signature: str = Field(description="该链接的结构指纹 (例如 'h2.title.post-title')")


class LinkGroup(BaseModel):
    """
    Represents a cluster of links sharing the same signature.
    (代表共享相同指纹的链接聚类。)
    """
    signature: str = Field(description="共享的结构指纹")
    count: int = Field(description="该指纹出现的次数")
    sample_links: List[LinkFingerprint] = Field(description="该组的链接示例 (最多5个)")


# --- End: 数据模型 ---


class ArticleListExtractor(IExtractor):
    """
    Extractor implementation for discovering and extracting article lists from a webpage.
    (用于发现和提取网页文章列表的提取器实现。)

    Implements the "link fingerprint" strategy.
    (实现了“链接指纹”策略。)
    """

    def __init__(self, verbose: bool = True, min_group_count: int = 3):
        """
        初始化列表提取器。
        :param min_group_count: 启发式猜测时，一个组至少需要多少个链接才被考虑。
        """
        super().__init__(verbose)
        self.min_group_count = min_group_count

    # --- 步骤 1: 生成链接指纹 ---

    def _get_structural_signature(self, tag: Tag) -> str:
        """
        Calculates the "structural signature" for an <a> tag based on its parent.
        (根据 <a> 标签的父节点计算其“结构指纹”。)

        The signature is `tag.class1.class2` of the immediate parent.
        (指纹是其直接父节点的 `标签名.class1.class2`。)
        """
        parent = tag.parent
        if not parent or parent.name == 'body':
            return 'body'

        name = parent.name
        classes = sorted(parent.get('class', []))

        if classes:
            return f"{name}.{'.'.join(classes)}"
        return name

    def _generate_fingerprints(self, soup: BeautifulSoup, base_url: str) -> List[LinkFingerprint]:
        """
        Step 1: Analyzes the DOM and generates a LinkFingerprint for every valid <a> tag.
        (步骤 1: 分析DOM，并为每个有效的 <a> 标签生成一个 LinkFingerprint。)
        """
        fingerprints = []
        seen_hrefs = set()

        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href'].strip()

            # 过滤无效链接
            if not href or href.startswith('#') or href.startswith('javascript:'):
                continue

            # 解析为绝对URL
            try:
                full_url = urljoin(base_url, href)
            except Exception:
                continue  # 忽略格式错误的URL

            # 过滤重复链接
            if full_url in seen_hrefs:
                continue
            seen_hrefs.add(full_url)

            text = a_tag.get_text(strip=True)
            signature = self._get_structural_signature(a_tag)

            fingerprints.append(LinkFingerprint(
                href=full_url,
                text=text,
                signature=signature
            ))

        return fingerprints

    # --- 步骤 2: 链接指纹聚类 ---

    def _cluster_fingerprints(self, fingerprints: List[LinkFingerprint]) -> List[LinkGroup]:
        """
        Step 2: Groups fingerprints by their signature.
        (步骤 2: 按指纹对链接进行分组。)
        """
        groups_map = defaultdict(list)
        for fp in fingerprints:
            groups_map[fp.signature].append(fp)

        link_groups = []
        for signature, fps_list in groups_map.items():
            link_groups.append(LinkGroup(
                signature=signature,
                count=len(fps_list),
                sample_links=fps_list[:5]  # 仅存储前5个作为示例
            ))

        # 按数量降序排序，最重要的组排在最前面
        link_groups.sort(key=lambda g: g.count, reverse=True)
        return link_groups

    # --- 步骤 3: 启发式猜测 ---

    def _guess_by_heuristics(self, groups: List[LinkGroup]) -> Optional[LinkGroup]:
        """
        Step 3: Guesses the main article list using a scoring-based heuristic.
        (步骤 3: 使用基于评分的启发式规则猜测主要文章列表。)

        This is the non-AI logic.
        (这是非AI逻辑。)
        """
        self._log("  [Heuristics] 启动启发式评分...", indent=1)
        best_group = None
        best_score = -99

        # 定义正面和负面信号词
        POSITIVE_SIG_KEYWORDS = ['article', 'post', 'item', 'entry', 'headline', 'title', 'feed', 'story']
        POSITIVE_TAG_KEYWORDS = ['h2', 'h3']
        NEGATIVE_SIG_KEYWORDS = [
            'nav', 'menu', 'header', 'head', 'foot', 'copyright', 'legal', 'privacy',
            'sidebar', 'aside', 'widget', 'ad', 'banner', 'comment', 'meta', 'tag', 'category'
        ]
        NEGATIVE_TEXT_KEYWORDS = ['关于我们', '联系我们', '首页', '隐私政策', 'home', 'about', 'contact', 'privacy']

        for group in groups:
            score = 0
            sig_lower = group.signature.lower()

            # 规则 1: 数量必须达标
            if group.count < self.min_group_count:
                self._log(f"    - [{sig_lower}]: 数量太少 ({group.count}), 跳过。", indent=1)
                continue

            score += group.count  # 数量越多，分数越高

            # 规则 2: 签名关键词
            if any(kw in sig_lower for kw in POSITIVE_SIG_KEYWORDS):
                score += 30
            if any(kw in sig_lower for kw in POSITIVE_TAG_KEYWORDS):
                score += 15
            if any(kw in sig_lower for kw in NEGATIVE_SIG_KEYWORDS):
                score -= 50

            # 规则 3: 样本链接文本
            if group.sample_links:
                avg_text_len = sum(len(fp.text) for fp in group.sample_links) / len(group.sample_links)

                # 标题通常不会太短
                if avg_text_len > 10:
                    score += 15
                if avg_text_len < 5:  # 可能是 "阅读更多" 或 "..."
                    score -= 10

                # 检查导航/页脚的常见文本
                sample_texts_lower = [fp.text.lower() for fp in group.sample_links]
                if any(kw in t for t in sample_texts_lower for kw in NEGATIVE_TEXT_KEYWORDS):
                    score -= 30

            self._log(f"    - [{sig_lower}]: 最终得分 {score}", indent=1)

            if score > best_score:
                best_score = score
                best_group = group

        if best_score <= 0:
            self._log("  [Heuristics] 启发式猜测失败：没有组的分数 > 0。", indent=1)
            return None

        self._log(f"  [Heuristics] 获胜者: {best_group.signature} (得分: {best_score})", indent=1)
        return best_group

    # --- 步骤 4: AI Prompt 准备 ---

    def _prepare_ai_prompt(self, groups: List[LinkGroup], page_title: str, page_url: str) -> str:
        """
        Step 4: Prepares the JSON payload and the system prompt for the AI.
        (步骤 4: 为AI准备JSON负载和系统提示。)
        """

        # 将Pydantic模型转换为字典
        groups_data = [g.model_dump() for g in groups]

        payload = {
            "page_url": page_url,
            "page_title": page_title,
            "link_groups": groups_data
        }

        json_payload = json.dumps(payload, indent=2, ensure_ascii=False)

        system_prompt = """你是一个专业的网页结构分析引擎。你的任务是分析一个JSON输入，该JSON代表了网页上所有链接的分组情况。你需要找出哪一个分组是该页面的**主要文章列表**。

**决策标准:**
1.  **排除导航和页脚：** 签名（signature）中包含`nav`, `menu`, `footer`, `copyright`的，或者链接文本（text）为“首页”、“关于我们”、“隐私政策”的，**不是**主列表。
2.  **排除侧边栏和部件：** 签名（signature）中包含`sidebar`, `widget`, `aside`, `ad`的，或者链接文本为“热门文章”、“标签云”的，**不是**主列表。
3.  **识别文章特征：**
    * `count`（数量）通常较高（例如 > 5）。
    * 链接文本（text）看起来像**文章标题**（例如：“xxx的评测”、“xxx宣布了新功能”）。
    * `href`（链接）看起来像**文章的永久链接**（例如：`/post/slug-name`或`/article/12345.html`），而不是分类链接（`/category/tech`）。
4.  **识别签名：** 主列表的签名通常是`article`, `post`, `item`, `entry`, `feed`或`h2`, `h3`等。

**任务:**
分析以下JSON数据，并**仅返回**你认为是**主要文章列表**的那个分组的`signature`字符串。如果找不到，请返回`null`。
"""

        full_prompt = f"{system_prompt}\n\n**输入数据:**\n```json\n{json_payload}\n```"
        return full_prompt

    # --- 辅助方法 ---

    def _find_group_by_signature(self, groups: List[LinkGroup], signature: str) -> Optional[LinkGroup]:
        """按签名查找已聚类的组。"""
        for group in groups:
            if group.signature == signature:
                return group
        return None

    def _extract_links_by_signature(self, soup: BeautifulSoup, signature: str, base_url: str) -> List[str]:
        """
        Using the winning signature, extract all corresponding links.
        (使用获胜的签名，提取所有对应的链接。)
        """
        self._log(f"  正在使用CSS选择器 '{signature}' 提取...", indent=2)
        parent_elements = soup.select(signature)

        final_links = []
        seen_hrefs = set()

        for parent in parent_elements:
            # 在父节点内查找第一个有效链接
            a_tag = parent.find('a', href=True)

            if a_tag:
                href = a_tag['href'].strip()
                if not href or href.startswith('#') or href.startswith('javascript:'):
                    continue

                try:
                    full_url = urljoin(base_url, href)
                    if full_url not in seen_hrefs:
                        final_links.append(full_url)
                        seen_hrefs.add(full_url)
                except Exception:
                    continue  # 忽略格式错误的URL

        return final_links

    # --- 主提取方法 ---

    def extract(self, content: bytes, url: str, **kwargs) -> ExtractionResult:
        """
        Orchestrates the entire list extraction process.
        (协调整个列表提取过程。)

        :param content: The raw HTML content as bytes. (原始HTML字节)
        :param url: The original URL. (原始URL)
        :param kwargs:
            - use_ai (bool): If True, triggers AI mode. (如果为True，触发AI模式)
            - ai_signature (str): The response from the AI (the winning signature). (AI的响应，即获胜的签名)
        :return: An ExtractionResult.
        """
        self.log_messages = []  # 重置日志
        self._log(f"开始列表提取: {url}")

        use_ai = kwargs.get('use_ai', False)
        ai_signature = kwargs.get('ai_signature', None)

        try:
            self._log("正在解析HTML (使用 lxml)...")
            soup = BeautifulSoup(content, 'lxml')
            page_title = soup.title.string.strip() if soup.title else ""

            # 步骤 1: 生成指纹
            self._log("步骤 1: 正在生成链接指纹...")
            fingerprints = self._generate_fingerprints(soup, url)
            if not fingerprints:
                return ExtractionResult(error="页面上未找到任何有效链接")
            self._log(f"  找到 {len(fingerprints)} 个有效链接。", indent=1)

            # 步骤 2: 聚类
            self._log("步骤 2: 正在聚类指纹...")
            groups = self._cluster_fingerprints(fingerprints)
            if not groups:
                return ExtractionResult(error="无法对链接进行聚类")
            self._log(f"  聚类为 {len(groups)} 个唯一的签名组。", indent=1)

            # --- 决策阶段 ---
            winning_group: Optional[LinkGroup] = None

            if use_ai:
                self._log("步骤 3: [AI 模式] 启动...")
                if ai_signature:
                    # AI模式 - 步骤 2: 接收到AI的签名
                    self._log(f"  接收到AI决策: '{ai_signature}'", indent=1)
                    winning_group = self._find_group_by_signature(groups, ai_signature)
                    if not winning_group:
                        return ExtractionResult(error=f"AI返回的签名 '{ai_signature}' 在聚类组中未找到。")
                else:
                    # AI模式 - 步骤 1: 生成Prompt
                    self._log("  未提供AI签名。正在生成Prompt...", indent=1)
                    prompt = self._prepare_ai_prompt(groups, page_title, url)
                    self._log("  已生成Prompt。请使用 metadata.ai_prompt 调用您的AI服务。", indent=1)
                    # 将groups也返回，以便AI调用失败时回退
                    groups_data = [g.model_dump() for g in groups]
                    return ExtractionResult(
                        markdown_content="# AI Prompt 已生成\n\n请查看 `metadata.ai_prompt` 字段，并使用AI服务获取 `signature`。",
                        metadata={
                            "title": "AI Prompt 请求",
                            "ai_prompt_required": True,
                            "ai_prompt": prompt,
                            "link_groups": groups_data
                        }
                    )
            else:
                # 启发式模式
                self._log("步骤 3: [启发式模式] 正在猜测主列表...")
                winning_group = self._guess_by_heuristics(groups)
                if not winning_group:
                    debug_info = "\n".join([f"  - {g.signature} (Count: {g.count})" for g in groups[:10]])
                    return ExtractionResult(error=f"启发式规则未能确定主列表。检测到的顶级组:\n{debug_info}")

            # --- 提取阶段 ---
            if not winning_group:
                # 这是一个理论上不应该发生的路径，但作为保险
                return ExtractionResult(error="未能确定获胜的链接组。")

            self._log(f"步骤 4: 获胜签名 '{winning_group.signature}' (数量: {winning_group.count})")
            self._log("步骤 5: 正在提取最终链接...")
            final_links = self._extract_links_by_signature(soup, winning_group.signature, url)

            if not final_links:
                return ExtractionResult(error=f"获胜签名 '{winning_group.signature}' 未能提取到任何链接。")

            self._log(f"  成功提取 {len(final_links)} 个链接。")

            # 构建 Markdown 输出
            md_content = f"# 提取的文章列表\n\n源: {url}\n签名: `{winning_group.signature}`\n\n"
            for link in final_links:
                # 尝试从指纹中找到原始文本，如果找不到就用URL作为文本
                link_text = next((fp.text for fp in winning_group.sample_links if fp.href == link), None)
                if not link_text or len(link_text) < 5:  # 如果文本太短或没有
                    md_content += f"- {link}\n"
                else:
                    md_content += f"- [{link_text}]({link})\n"

            metadata = {
                "title": f"文章列表: {page_title if page_title else url}",
                "source_url": url,
                "winning_signature": winning_group.signature,
                "extracted_links_count": len(final_links),
                "extracted_links": final_links,
                "all_groups": [g.model_dump() for g in groups]  # 包含所有组的调试信息
            }

            return ExtractionResult(markdown_content=md_content, metadata=metadata)

        except Exception as e:
            self._log(f"提取过程中发生严重错误: {e}")
            import traceback
            self._log(traceback.format_exc())
            return ExtractionResult(error=f"提取失败: {e}")


# --- 示例用法 (Example Usage) ---

if __name__ == "__main__":

    # --- 模拟的HTML内容 (Mock HTML Content) ---
    # 这是一个包含 导航、主列表、侧边栏、页脚 的典型页面
    MOCK_HTML_CONTENT = """
    <html>
        <head><title>示例新闻页面</title></head>
        <body>
            <header>
                <nav class="main-menu">
                    <ul>
                        <li class="nav-item"><a href="/">首页</a></li>
                        <li class="nav-item"><a href="/about">关于</a></li>
                        <li class="nav-item"><a href="/contact">联系</a></li>
                    </ul>
                </nav>
            </header>

            <div class="content-area">
                <main class="article-feed">
                    <article class="post-item">
                        <h2 class="post-title"><a href="/post/article-1">文章标题一</a></h2>
                        <p class="excerpt">这是第一篇文章的摘要...</p>
                    </article>
                    <article class="post-item">
                        <h2 class="post-title"><a href="/post/article-2">文章标题二</a></h2>
                        <p class="excerpt">这是第二篇文章的摘要...</p>
                    </article>
                    <article class="post-item">
                        <h2 class="post-title"><a href="/post/article-3">文章标题三：一个更长的标题示例</a></h2>
                        <p class="excerpt">这是第三篇文章的摘要...</p>
                    </article>
                </main>

                <aside class="sidebar">
                    <h3>热门文章</h3>
                    <ul class="widget-list">
                        <li class="widget-item"><a href="/popular/p1">热门文章A</a></li>
                        <li class="widget-item"><a href="/popular/p2">热门文章B</a></li>
                    </ul>
                </aside>
            </div>

            <footer class="site-footer">
                <div class="footer-links">
                    <a href="/privacy">隐私政策</a>
                    <a href="/terms">服务条款</a>
                </div>
            </footer>
        </body>
    </html>
    """.encode('utf-8')

    MOCK_URL = "https://example.com/news"

    # --- 1. 启发式模式 (Heuristic Mode) ---
    print("=" * 50)
    print("测试 1: 启发式模式 (Heuristic Mode)")
    print("=" * 50)

    # verbose=True 会打印详细的日志
    heuristic_extractor = ArticleListExtractor(verbose=True, min_group_count=3)

    result_heuristic = heuristic_extractor.extract(
        content=MOCK_HTML_CONTENT,
        url=MOCK_URL
    )

    print("\n--- 启发式结果 (str) ---")
    print(result_heuristic)
    # print("\n--- 启发式元数据 (metadata) ---")
    # print(json.dumps(result_heuristic.metadata, indent=2, ensure_ascii=False, default=str))

    # --- 2. AI 模式 - 步骤 1: 获取 Prompt ---
    print("\n" + "=" * 50)
    print("测试 2: AI 模式 - 步骤 1 (获取 Prompt)")
    print("=" * 50)

    ai_extractor = ArticleListExtractor(verbose=True)

    result_ai_prompt = ai_extractor.extract(
        content=MOCK_HTML_CONTENT,
        url=MOCK_URL,
        use_ai=True  # 触发AI模式
    )

    print("\n--- AI Prompt 结果 (str) ---")
    print(result_ai_prompt)

    # 打印将要发送给AI的Prompt
    if result_ai_prompt.metadata.get("ai_prompt_required"):
        print("\n--- [ 您需要发送给AI的PROMPT ] ---")
        print(result_ai_prompt.metadata["ai_prompt"])
        print("--- [ AI PROMPT 结束 ] ---")

    # --- 3. AI 模式 - 步骤 2: 传入 AI 的响应 ---
    print("\n" + "=" * 50)
    print("测试 3: AI 模式 - 步骤 2 (传入AI响应)")
    print("=" * 50)

    # 假设您的AI服务分析了上面的JSON，并返回了这个签名：
    MOCK_AI_RESPONSE_SIGNATURE = "h2.post-title"

    print(f"*** 模拟AI服务返回: '{MOCK_AI_RESPONSE_SIGNATURE}' ***\n")

    result_ai_final = ai_extractor.extract(
        content=MOCK_HTML_CONTENT,
        url=MOCK_URL,
        use_ai=True,
        ai_signature=MOCK_AI_RESPONSE_SIGNATURE  # 传入AI的决策
    )

    print("\n--- AI 最终结果 (str) ---")
    print(result_ai_final)
