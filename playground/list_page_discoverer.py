import json
import datetime
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Set, Tuple
from collections import defaultdict
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field


# --- Begin: 链接指纹的数据模型 (Data Models) ---

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


# --- Begin: 用户提供的基类 (User-Provided Base Class) ---
# (为了让文件可独立运行，我假设一个 Fetcher 接口并复制 IDiscoverer)

class Fetcher(ABC):
    """
    Abstract base class for a network fetcher.
    (网络获取器的抽象基类。)
    """

    @abstractmethod
    def get_content(self, url: str) -> Optional[bytes]:
        """
        Fetches the content of a URL and returns it as bytes.
        (获取URL的内容并以字节形式返回。)
        """
        pass


class IDiscoverer(ABC):
    """
    Abstract base class for a discovery component.
    (发现组件的抽象基类)
    """

    def __init__(self, fetcher: "Fetcher", verbose: bool = True):
        self.fetcher = fetcher
        self.verbose = verbose
        self.log_messages: List[str] = []

    @abstractmethod
    def _log(self, message: str, indent: int = 0):
        pass

    @abstractmethod
    def discover_channels(self,
                          entry_point: Any,
                          start_date: Optional[datetime.datetime] = None,
                          end_date: Optional[datetime.datetime] = None
                          ) -> List[str]:
        pass

    @abstractmethod
    def get_articles_for_channel(self, channel_url: str) -> List[str]:
        pass

    def get_content_str(self, url: str) -> str:
        self.log_messages.clear()
        self._log(f"Fetching raw content for: {url}")
        content = self.fetcher.get_content(url)
        if content:
            try:
                return content.decode('utf-8', errors='ignore')
            except Exception as e:
                self._log(f"Error decoding content: {e}")
                return f"Error decoding content: {e}"
        return f"Failed to fetch content from {url}"


# --- End: 用户提供的基类 ---


class ListPageDiscoverer(IDiscoverer):
    """
    Discovers article URLs from a single list page using the "link fingerprint" strategy.
    (使用“链接指纹”策略从单个列表页发现文章URL。)
    """

    def __init__(self,
                 fetcher: "Fetcher",
                 verbose: bool = True,
                 min_group_count: int = 3,
                 ai_signature: Optional[str] = None):
        """
        Initializes the list page discoverer.
        (初始化列表页发现器。)

        :param fetcher: An instance of a Fetcher implementation.
        :param verbose: Toggles detailed logging.
        :param min_group_count: Minimum links in a group to be considered by heuristics.
                                (启发式规则考虑的组的最小链接数。)
        :param ai_signature: (Optional) A pre-determined signature (e.g., from an AI)
                             to use, skipping heuristics.
                             (（可选）一个预先确定的签名（例如来自AI），
                              用于跳过启发式规则。)
        """
        super().__init__(fetcher, verbose)
        self.log_messages: List[str] = []
        self.min_group_count = min_group_count
        self.ai_signature = ai_signature

        # 优化：缓存页面分析结果 (soup, groups)
        self.analysis_cache: Dict[str, Tuple[BeautifulSoup, List[LinkGroup]]] = {}

    def _log(self, message: str, indent: int = 0):
        """
        Provides a unified logging mechanism.
        (提供统一的日志记录机制。)
        """
        log_msg = f"{' ' * (indent * 4)}{message}"
        self.log_messages.append(log_msg)
        if self.verbose:
            print(log_msg)

    # --- 核心逻辑: 指纹分析 (Core Logic: Fingerprint Analysis) ---

    def _analyze_page(self, url: str) -> Tuple[Optional[BeautifulSoup], List[LinkGroup]]:
        """
        Internal helper to fetch, parse, and analyze a page.
        (获取、解析和分析页面的内部辅助函数。)

        Results are cached to avoid re-fetching/re-parsing.
        (结果被缓存以避免重复获取/解析。)
        """
        if url in self.analysis_cache:
            self._log(f"  [Cache] 命中: {url}", indent=1)
            return self.analysis_cache[url]

        self._log(f"  [Fetch] 开始获取: {url}", indent=1)
        content = self.fetcher.get_content(url)
        if not content:
            self._log(f"  [Fetch] 失败: 未能获取内容。", indent=1)
            return None, []

        self._log(f"  [Parse] 正在解析HTML (lxml)...", indent=1)
        soup = BeautifulSoup(content, 'lxml')

        self._log(f"  [Analyze] 步骤 1: 生成指纹...", indent=1)
        fingerprints = self._generate_fingerprints(soup, url)
        if not fingerprints:
            self._log(f"  [Analyze] 页面上未找到有效链接。", indent=1)
            return soup, []

        self._log(f"  [Analyze] 步骤 2: 聚类指纹...", indent=1)
        groups = self._cluster_fingerprints(fingerprints)

        # 缓存结果
        self.analysis_cache[url] = (soup, groups)
        return soup, groups

    def _get_structural_signature(self, tag: Tag) -> str:
        parent = tag.parent
        if not parent or parent.name == 'body':
            return 'body'
        name = parent.name
        classes = sorted(parent.get('class', []))
        return f"{name}.{'.'.join(classes)}" if classes else name

    def _generate_fingerprints(self, soup: BeautifulSoup, base_url: str) -> List[LinkFingerprint]:
        fingerprints = []
        seen_hrefs = set()
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href'].strip()
            if not href or href.startswith('#') or href.startswith('javascript:'):
                continue
            try:
                full_url = urljoin(base_url, href)
            except Exception:
                continue
            if full_url in seen_hrefs:
                continue
            seen_hrefs.add(full_url)
            text = a_tag.get_text(strip=True)
            signature = self._get_structural_signature(a_tag)
            fingerprints.append(LinkFingerprint(href=full_url, text=text, signature=signature))
        return fingerprints

    def _cluster_fingerprints(self, fingerprints: List[LinkFingerprint]) -> List[LinkGroup]:
        groups_map = defaultdict(list)
        for fp in fingerprints:
            groups_map[fp.signature].append(fp)
        link_groups = [
            LinkGroup(signature=sig, count=len(fps), sample_links=fps[:5])
            for sig, fps in groups_map.items()
        ]
        link_groups.sort(key=lambda g: g.count, reverse=True)
        return link_groups

    # --- 核心逻辑: 决策 (Core Logic: Decision) ---

    def _guess_by_heuristics(self, groups: List[LinkGroup]) -> Optional[LinkGroup]:
        self._log("    [Decision] 启动启发式评分...", indent=1)
        best_group = None
        best_score = -99

        POSITIVE_SIG_KEYWORDS = ['article', 'post', 'item', 'entry', 'headline', 'title', 'feed', 'story']
        POSITIVE_TAG_KEYWORDS = ['h2', 'h3']
        NEGATIVE_SIG_KEYWORDS = ['nav', 'menu', 'header', 'foot', 'copyright', 'sidebar', 'aside', 'widget', 'ad',
                                 'meta', 'tag', 'category']
        NEGATIVE_TEXT_KEYWORDS = ['关于我们', '联系我们', '首页', '隐私政策', 'home', 'about', 'contact', 'privacy']

        for group in groups:
            score = 0
            sig_lower = group.signature.lower()
            if group.count < self.min_group_count:
                continue

            score += group.count
            if any(kw in sig_lower for kw in POSITIVE_SIG_KEYWORDS): score += 30
            if any(kw in sig_lower for kw in POSITIVE_TAG_KEYWORDS): score += 15
            if any(kw in sig_lower for kw in NEGATIVE_SIG_KEYWORDS): score -= 50

            if group.sample_links:
                avg_text_len = sum(len(fp.text) for fp in group.sample_links) / len(group.sample_links)
                if avg_text_len > 10: score += 15
                if avg_text_len < 5: score -= 10
                sample_texts_lower = [fp.text.lower() for fp in group.sample_links]
                if any(kw in t for t in sample_texts_lower for kw in NEGATIVE_TEXT_KEYWORDS): score -= 30

            if score > best_score:
                best_score = score
                best_group = group

        if best_score <= 0:
            self._log("    [Decision] 启发式猜测失败：没有组的分数 > 0。", indent=1)
            return None

        self._log(f"    [Decision] 启发式获胜者: {best_group.signature} (得分: {best_score})", indent=1)
        return best_group

    def _find_group_by_signature(self, groups: List[LinkGroup], signature: str) -> Optional[LinkGroup]:
        for group in groups:
            if group.signature == signature:
                return group
        return None

    # --- 核心逻辑: 提取 (Core Logic: Extraction) ---

    def _extract_links_by_signature(self, soup: BeautifulSoup, signature: str, base_url: str) -> List[str]:
        self._log(f"    [Extract] 正在使用CSS选择器 '{signature}' 提取链接...", indent=1)
        parent_elements = soup.select(signature)
        final_links = []
        seen_hrefs = set()
        for parent in parent_elements:
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
                    continue
        self._log(f"    [Extract] 成功提取 {len(final_links)} 个链接。", indent=1)
        return final_links

    # --- IDiscoverer 接口实现 (Interface Implementation) ---

    def discover_channels(self,
                          entry_point: Any,
                          start_date: Optional[datetime.datetime] = None,
                          end_date: Optional[datetime.datetime] = None
                          ) -> List[str]:
        """
        Stage 1: Discovers channels. For ListPageDiscoverer, the
        entry point URL *is* the one and only channel.
        (阶段1：发现频道。对于ListPageDiscoverer，
         入口点URL *是* 唯一的一个频道。)
        """
        self.log_messages.clear()
        self._log(f"开始频道发现: {entry_point}")

        if not isinstance(entry_point, str) or not entry_point.startswith(('http://', 'https://')):
            self._log(f"  错误: 入口点必须是一个有效的URL字符串。", indent=1)
            return []

        self._log(f"  ListPageDiscoverer 将入口点视为唯一频道。", indent=1)
        self._log(f"  (日期过滤器 start_date/end_date 在此发现器中被忽略)", indent=1)

        return [entry_point]

    def get_articles_for_channel(self, channel_url: str) -> List[str]:
        """
        Stage 2: Fetches and parses the list page (channel) to extract
        all individual article URLs.
        (阶段2：获取并解析列表页（频道）以提取
         所有单独的文章URL。)
        """
        self.log_messages.clear()
        self._log(f"开始从频道 (列表页) 获取文章: {channel_url}")

        # 步骤 1 & 2: 分析页面 (获取、解析、聚类)
        # 这将使用缓存 (如果存在)
        soup, groups = self._analyze_page(channel_url)

        if not soup or not groups:
            self._log(f"  分析失败或未找到链接组。", indent=1)
            return []

        # 步骤 3: 决策 (AI签名优先，否则回退到启发式)
        winning_group: Optional[LinkGroup] = None

        if self.ai_signature:
            self._log(f"  [Decision] 正在使用预配置的 AI 签名: '{self.ai_signature}'", indent=1)
            winning_group = self._find_group_by_signature(groups, self.ai_signature)
            if not winning_group:
                self._log(f"  [Decision] 错误: AI签名 '{self.ai_signature}' 在组中未找到。", indent=1)
                # (可选：可以回退到启发式，但现在我们保持严格)
                return []
        else:
            self._log(f"  [Decision] 未提供 AI 签名，正在使用启发式规则...", indent=1)
            winning_group = self._guess_by_heuristics(groups)
            if not winning_group:
                self._log(f"  [Decision] 启发式规则未能找到获胜组。", indent=1)
                return []

        # 步骤 4: 提取
        self._log(f"  [Extract] 获胜签名: {winning_group.signature} (Count: {winning_group.count})", indent=1)
        final_links = self._extract_links_by_signature(soup, winning_group.signature, channel_url)

        return final_links

    # --- AI 辅助方法 (AI Helper Method) ---

    def _prepare_ai_prompt(self, groups: List[LinkGroup], page_title: str, page_url: str) -> str:
        """
        Prepares the JSON payload and the system prompt for the AI.
        (为AI准备JSON负载和系统提示。)
        """
        groups_data = [g.model_dump() for g in groups]
        payload = {"page_url": page_url, "page_title": page_title, "link_groups": groups_data}
        json_payload = json.dumps(payload, indent=2, ensure_ascii=False)

        system_prompt = """你是一个专业的网页结构分析引擎。你的任务是分析一个JSON输入，该JSON代表了网页上所有链接的分组情况。你需要找出哪一个分组是该页面的**主要文章列表**。

**决策标准:**
1.  **排除导航和页脚：** 签名（signature）中包含`nav`, `menu`, `footer`, `copyright`的，或者链接文本（text）为“首页”、“关于我们”、“隐私政策”的，**不是**主列表。
2.  **排除侧边栏和部件：** 签名（signature）中包含`sidebar`, `widget`, `aside`, `ad`的，或者链接文本为“热门文章”、“标签云”的，**不是**主列表。
3.  **识别文章特征：**
    * `count`（数量）通常较高（例如 > 5）。
    * 链接文本（text）看起来像**文章标题**（例如：“xxx的评测”、“xxx宣布了新功能”）。
    * `href`（链接）看起来像**文章的永久链接**（例如：`/post/slug-name`或`/article/12345.html`），而不是分类链接（`/category/tech`）。
4.  **识别签名：** 主列表的签名通常是`article`, `post`, 'item', 'entry', 'feed'或`h2`, `h3`等。

**任务:**
分析以下JSON数据，并**仅返回**你认为是**主要文章列表**的那个分组的`signature`字符串。如果找不到，请返回`null`。
"""
        return f"{system_prompt}\n\n**输入数据:**\n```json\n{json_payload}\n```"

    def generate_ai_discovery_prompt(self, entry_point_url: str) -> Optional[str]:
        """
        (Helper) Analyzes a page and returns the AI prompt needed to find the signature.
        (（辅助函数）分析页面并返回寻找签名所需的AI提示。)

        This is a helper method *outside* the IDiscoverer interface,
        used for the one-time setup process.
        (这是一个 IDiscoverer 接口之外的辅助方法，
         用于一次性的设置过程。)
        """
        self.log_messages.clear()
        self._log(f"正在为AI生成发现提示: {entry_point_url}")

        # _analyze_page 会获取、解析、聚类并缓存结果
        soup, groups = self._analyze_page(entry_point_url)

        if not soup:
            self._log(f"  错误: 无法获取或解析页面。", indent=1)
            return None
        if not groups:
            self._log(f"  错误: 页面上未找到链接组。", indent=1)
            return None

        page_title = soup.title.string.strip() if soup.title else ""

        prompt = self._prepare_ai_prompt(groups, page_title, entry_point_url)
        self._log(f"  成功生成AI Prompt。", indent=1)
        return prompt


# --- 示例用法 (Example Usage) ---

# 1. 创建一个模拟的 Fetcher
class MockFetcher(Fetcher):
    MOCK_HTML_CONTENT = """
    <html>
        <head><title>示例新闻页面</title></head>
        <body>
            <nav class="main-menu"><li class="nav-item"><a href="/">首页</a></li></nav>
            <main class="article-feed">
                <article class="post-item"><h2 class="post-title"><a href="/post/article-1">文章标题一</a></h2></article>
                <article class="post-item"><h2 class="post-title"><a href="/post/article-2">文章标题二</a></h2></article>
                <article class="post-item"><h2 class="post-title"><a href="/post/article-3">文章标题三</a></h2></article>
            </main>
            <aside class="sidebar"><ul class="widget-list"><li class="widget-item"><a href="/popular/p1">热门文章A</a></li></ul></aside>
            <footer class="site-footer"><div class="footer-links"><a href="/privacy">隐私政策</a></div></footer>
        </body>
    </html>
    """.encode('utf-8')

    def get_content(self, url: str) -> Optional[bytes]:
        print(f"[MockFetcher] 正在获取: {url}")
        if url == "https://example.com/news":
            return self.MOCK_HTML_CONTENT
        return None


if __name__ == "__main__":

    mock_fetcher = MockFetcher()
    ENTRY_URL = "https://example.com/news"

    # --- 流程 1: 启发式模式 (Heuristic Mode) ---
    print("=" * 50)
    print("流程 1: 启发式模式 (Heuristic Mode)")
    print("=" * 50)

    # verbose=True 会打印详细的日志
    heuristic_discoverer = ListPageDiscoverer(mock_fetcher, verbose=True, min_group_count=3)

    # 阶段 1: 发现频道
    channels_h = heuristic_discoverer.discover_channels(ENTRY_URL)
    print(f"\n发现的频道: {channels_h}")

    # 阶段 2: 从频道获取文章
    if channels_h:
        articles_h = heuristic_discoverer.get_articles_for_channel(channels_h[0])
        print(f"\n从频道提取的文章 (Heuristic):")
        for url in articles_h:
            print(f"  - {url}")

    # --- 流程 2: AI 模式 (AI Mode) ---
    print("\n" + "=" * 50)
    print("流程 2: AI 模式 (获取Prompt -> 配置 -> 运行)")
    print("=" * 50)

    # 步骤 2a: 使用一个临时发现器来生成Prompt
    print("--- 步骤 2a: 生成 AI Prompt ---")
    prompt_generator = ListPageDiscoverer(mock_fetcher, verbose=True)
    ai_prompt = prompt_generator.generate_ai_discovery_prompt(ENTRY_URL)

    print("\n[ 您需要发送给AI的PROMPT ]:")
    print(ai_prompt)
    print("[ AI PROMPT 结束 ]")

    # 步骤 2b: 假设AI返回了签名
    MOCK_AI_RESPONSE_SIGNATURE = "h2.post-title"
    print(f"\n*** 模拟AI服务返回: '{MOCK_AI_RESPONSE_SIGNATURE}' ***\n")

    # 步骤 2c: 创建一个 *配置了AI签名* 的新发现器
    print("--- 步骤 2c: 使用配置了AI签名的发现器 ---")
    ai_discoverer = ListPageDiscoverer(
        mock_fetcher,
        verbose=True,
        ai_signature=MOCK_AI_RESPONSE_SIGNATURE  # 注入AI的决策
    )

    # 阶段 1: 发现频道
    channels_ai = ai_discoverer.discover_channels(ENTRY_URL)
    print(f"\n发现的频道: {channels_ai}")

    # 阶段 2: 从频道获取文章
    if channels_ai:
        # 这次调用将使用缓存的分析结果，但应用 AI 决策
        articles_ai = ai_discoverer.get_articles_for_channel(channels_ai[0])
        print(f"\n从频道提取的文章 (AI-Configured):")
        for url in articles_ai:
            print(f"  - {url}")
