import requests
import xml.etree.ElementTree as ET
from usp.tree import sitemap_from_str
from urllib.parse import urlparse, urljoin
import re
from typing import Set, List, Dict, Any, Optional, Deque
from collections import deque


class SitemapDiscoverer:
    """
    一个通用的Sitemap发现器。
    它会自动尝试从 robots.txt 发现Sitemap，并回退到猜测默认路径。
    它使用一个队列来递归处理Sitemap索引。
    它优先使用 'ultimate-sitemap-parser' 库，并在失败时自动回退到手动的
    'xml.etree.ElementTree' 解析，以确保最大的兼容性和健壮性。
    """

    # --- 1. 配置 ---

    # 必须模拟浏览器，否则很多网站会返回 403 Forbidden
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    # XML 命名空间，Sitemap 的标准
    NAMESPACES = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

    def __init__(self, verbose: bool = True):
        """
        初始化发现器
        :param verbose: 是否打印详细的日志信息
        """
        self.verbose = verbose
        self.all_article_urls: Set[str] = set()
        self.to_process_queue: Deque[str] = deque()
        self.processed_sitemaps: Set[str] = set()
        self.sitemap_entries_found: List[str] = []

    def _log(self, message: str, indent: int = 0):
        """统一的日志打印函数"""
        if self.verbose:
            print(f"{' ' * (indent * 4)}{message}")

    def _get_content(self, url: str) -> Optional[bytes]:
        """
        通用的内容抓取函数
        - 返回 bytes (用于XML解析)
        - 返回 None (如果失败)
        """
        try:
            response = requests.get(url, headers=self.HEADERS, timeout=10)
            response.raise_for_status()  # 确保请求成功 (例如 200 OK)
            return response.content
        except requests.exceptions.RequestException as e:
            self._log(f"[Request Error] 抓取 {url} 失败: {e}", 1)
            return None

    def _discover_sitemap_entry_points(self, homepage_url: str) -> List[str]:
        """
        步骤一：自动发现Sitemap入口
        它会模拟搜索引擎爬虫的行为
        """
        self._log(f"正在为 {homepage_url} 自动发现Sitemap...")

        try:
            parsed_home = urlparse(homepage_url)
            base_url = f"{parsed_home.scheme}://{parsed_home.netloc}"
        except Exception as e:
            self._log(f"[Error] 无法解析主页URL: {e}")
            return []

        # 路径 1: 检查 robots.txt (首选)
        robots_url = urljoin(base_url, '/robots.txt')
        self._log(f"检查 robots.txt: {robots_url}", 1)
        robots_content = self._get_content(robots_url)

        sitemap_urls = []
        if robots_content:
            try:
                sitemap_urls = re.findall(
                    r"^Sitemap:\s*(.+)$",
                    robots_content.decode('utf-8'),
                    re.IGNORECASE | re.MULTILINE
                )
                sitemap_urls = [url.strip() for url in sitemap_urls]

                if sitemap_urls:
                    self._log(f"在 robots.txt 中发现 {len(sitemap_urls)} 个Sitemap: {sitemap_urls}", 1)
                    return sitemap_urls

            except Exception as e:
                self._log(f"解析 robots.txt 出错: {e}", 1)

        # 路径 2: 猜测默认路径 (Fallback)
        self._log("未在 robots.txt 中发现Sitemap，开始猜测默认路径...", 1)
        return [
            urljoin(base_url, '/sitemap_index.xml'),
            urljoin(base_url, '/sitemap.xml')
        ]

    def _parse_sitemap_xml(self, xml_content: bytes, sitemap_url: str) -> Dict[str, List[str]]:
        """
        解析Sitemap XML内容。
        这是一个核心步骤，它实现了 "USP库优先，手动解析回退" 的逻辑。

        :param xml_content: 抓取到的XML二进制内容
        :param sitemap_url: 当前Sitemap的URL (仅用于日志)
        :return: 一个字典，包含 'pages' (页面URL列表) 和 'sub_sitemaps' (子Sitemap URL列表)
        """
        pages: List[str] = []
        sub_sitemaps: List[str] = []

        try:
            # -----------------
            # 方案 A: 优先尝试 USP 库 (简单、自动)
            # -----------------
            self._log("    尝试使用 [ultimate-sitemap-parser] 库解析...", 1)
            parsed_sitemap = sitemap_from_str(xml_content.decode('utf-8', errors='ignore'))

            # 1. 提取页面 (<urlset>)
            for page in parsed_sitemap.all_pages():
                pages.append(page.url)

            # 2. 提取子Sitemap (<sitemapindex>)
            for sub_sitemap in parsed_sitemap.all_sub_sitemaps():
                sub_sitemaps.append(sub_sitemap.url)

            self._log(f"    [USP 成功] 发现 {len(pages)} 个页面 和 {len(sub_sitemaps)} 个子Sitemap。", 1)

        except Exception as e:
            # -----------------
            # 方案 B: USP 库解析失败 (例如人民网的索引)，执行手动回退
            # -----------------
            self._log(f"    [USP 失败] 库解析出错: {e}", 1)
            self._log("    --> 启动 [手动 ElementTree] 回退方案...", 1)

            try:
                root = ET.fromstring(xml_content)

                # 1. 手动解析索引文件 (<sitemap>)
                index_nodes = root.findall('ns:sitemap', self.NAMESPACES)
                if index_nodes:
                    for node in index_nodes:
                        loc = node.find('ns:loc', self.NAMESPACES)
                        if loc is not None and loc.text:
                            sub_sitemaps.append(loc.text)  # 添加子Sitemap
                    self._log(f"    [手动回退] 发现 {len(sub_sitemaps)} 个子Sitemap。", 1)

                # 2. 手动解析页面文件 (<url>)
                url_nodes = root.findall('ns:url', self.NAMESPACES)
                if url_nodes:
                    for node in url_nodes:
                        loc = node.find('ns:loc', self.NAMESPACES)
                        if loc is not None and loc.text:
                            pages.append(loc.text)  # 添加页面URL
                    self._log(f"    [手动回退] 发现 {len(pages)} 个页面。", 1)

                if not index_nodes and not url_nodes:
                    self._log("    [手动回退] 失败: XML中未找到 <sitemap> 或 <url> 标签。", 1)

            except ET.ParseError as xml_e:
                self._log(f"    [手动回退] 失败: 无法解析XML。 错误: {xml_e}", 1)

        return {'pages': pages, 'sub_sitemaps': sub_sitemaps}

    def discover_urls(self, homepage_url: str) -> Dict[str, Any]:
        """
        公开的主方法：执行Sitemap发现与解析。

        :param homepage_url: 要分析的网站主页URL
        :return: 包含分析结果的字典
        """
        # --- 0. 重置状态 ---
        self.all_article_urls.clear()
        self.to_process_queue.clear()
        self.processed_sitemaps.clear()
        self.sitemap_entries_found = []

        # --- 1. 自动发现入口 ---
        initial_sitemaps = self._discover_sitemap_entry_points(homepage_url)
        if not initial_sitemaps:
            self._log("未能发现任何Sitemap入口。")
            return {
                'homepage': homepage_url,
                'status': 'failure',
                'error': 'No sitemap entry points found.',
                'total_urls_found': 0,
                'sitemap_entries_processed': [],
                'article_urls': []
            }

        self.to_process_queue.extend(initial_sitemaps)

        # --- 2. 循环处理Sitemap队列 ---
        while self.to_process_queue:
            # 从队列中取出一个URL
            sitemap_url = self.to_process_queue.popleft()

            if sitemap_url in self.processed_sitemaps:
                continue
            self.processed_sitemaps.add(sitemap_url)
            self.sitemap_entries_found.append(sitemap_url)  # 记录处理过的Sitemap

            self._log(f"\n--- 正在处理Sitemap: {sitemap_url} ---")

            # 抓取Sitemap的XML内容
            xml_content = self._get_content(sitemap_url)
            if not xml_content:
                self._log("    抓取失败，跳过。", 1)
                continue

            # --- 3. 解析与回退 ---
            parse_result = self._parse_sitemap_xml(xml_content, sitemap_url)

            # 将新发现的页面URL添加到总集合中 (set 自动去重)
            if parse_result['pages']:
                self.all_article_urls.update(parse_result['pages'])

            # 将新发现的子Sitemap添加回队列
            if parse_result['sub_sitemaps']:
                self.to_process_queue.extend(parse_result['sub_sitemaps'])

        # --- 4. 最终结果 ---
        self._log(f"\n==========================================")
        self._log(f"Sitemap 发现与解析全部完成。")
        self._log(f"为 {homepage_url} 共找到 {len(self.all_article_urls)} 个独立页面。")

        return {
            'homepage': homepage_url,
            'status': 'success' if self.all_article_urls else 'failure',
            'error': None,
            'total_urls_found': len(self.all_article_urls),
            'sitemap_entries_processed': self.sitemap_entries_found,
            'article_urls': list(self.all_article_urls)  # 返回列表而非set
        }


# --------------------------------------------------------------------
#
# 示例：如何调用这个通用的过程
#
# --------------------------------------------------------------------
if __name__ == "__main__":

    # 实例化我们的发现器
    # verbose=True 会打印详细日志，方便调试
    # verbose=False 只会安静地执行
    discoverer = SitemapDiscoverer(verbose=True)

    # 在这里添加您想尝试的所有网站
    websites_to_analyze = [
        "http://www.people.com.cn",  # (我们知道这个需要回退)
        "https://www.cnblogs.com",  # (标准 sitemap.xml)
        "https://www.theverge.com",  # (标准 sitemap_index.xml)
        "https://www.xinhuanet.com",  # (新华网)
        "http://www.gov.cn",  # (中国政府网)
        # "https://www.bbc.com"        # (可以继续添加...)
    ]

    # 存储所有网站的分析结果
    all_results = []

    for site in websites_to_analyze:
        print(f"\n\n{'=' * 25} 正在分析: {site} {'=' * 25}")

        # 调用通用过程
        result = discoverer.discover_urls(site)
        all_results.append(result)

        # 打印简短的分析摘要
        print(f"\n--- 分析摘要 for {site} ---")
        print(f"状态: {result['status']}")
        print(f"处理的Sitemap入口数: {len(result['sitemap_entries_processed'])}")
        print(f"总计找到的URL: {result['total_urls_found']}")

        # 抽样展示5个URL
        if result['article_urls']:
            print("--- 抽样展示 5 个URL ---")
            sample = result['article_urls'][:5]
            for url in sample:
                print(url)
        print(f"{'=' * 60}\n\n")

    # 在这里，`all_results` 变量中包含了所有网站的详细分析结果
    # 你可以将其保存到数据库或 JSON 文件中
    # import json
    # with open('sitemap_analysis_results.json', 'w', encoding='utf-8') as f:
    #     json.dump(all_results, f, indent=4, ensure_ascii=False)
    # print("所有结果已保存到 sitemap_analysis_results.json")
