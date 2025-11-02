import requests
import xml.etree.ElementTree as ET
from usp.tree import sitemap_from_str
from urllib.parse import urlparse, urljoin
import re  # 用于解析 robots.txt

# --- 1. 配置 ---

# 必须模拟浏览器，否则 403 Forbidden
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# XML 命名空间
NAMESPACES = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}


# --- 2. 辅助函数 ---

def get_content(url, headers):
    """
    通用的内容抓取函数
    - 返回 bytes (用于XML解析)
    - 返回 None (如果失败)
    """
    try:
        response = requests.get(url, headers=headers, timeout=10)
        # 确保请求成功 (例如 200 OK)
        response.raise_for_status()
        return response.content
    except requests.exceptions.RequestException as e:
        print(f"[Request Error] 抓取 {url} 失败: {e}")
        return None


def discover_sitemap_urls(homepage_url, headers):
    """
    步骤一：自动发现Sitemap入口
    它会模拟搜索引擎爬虫的行为
    """
    print(f"正在为 {homepage_url} 自动发现Sitemap...")

    # 解析主页URL，获取根域名 (e.g., "http://www.people.com.cn")
    parsed_home = urlparse(homepage_url)
    base_url = f"{parsed_home.scheme}://{parsed_home.netloc}"

    # 路径 1: 检查 robots.txt (首选)
    robots_url = urljoin(base_url, '/robots.txt')
    robots_content = get_content(robots_url, headers)

    sitemap_urls = []
    if robots_content:
        try:
            # 使用正则表达式安全地查找所有 Sitemap 指令
            # (忽略大小写, 匹配行首的 'sitemap:' )
            sitemap_urls = re.findall(
                r"^Sitemap:\s*(.+)$",
                robots_content.decode('utf-8'),
                re.IGNORECASE | re.MULTILINE
            )
            sitemap_urls = [url.strip() for url in sitemap_urls]

            if sitemap_urls:
                print(f"在 robots.txt 中发现 {len(sitemap_urls)} 个Sitemap: {sitemap_urls}")
                return sitemap_urls

        except Exception as e:
            print(f"解析 robots.txt 出错: {e}")

    # 路径 2: 猜测默认路径 (Fallback)
    # (如果 robots.txt 不存在或没有Sitemap指令)
    print("未在 robots.txt 中发现Sitemap，开始猜测默认路径...")

    # 返回最常见的两个猜测路径
    return [
        urljoin(base_url, '/sitemap_index.xml'),
        urljoin(base_url, '/sitemap.xml')
    ]


# --- 3. 主流程 ---

def main():
    # homepage = "http://www.people.com.cn"
    homepage = "https://www.cnblogs.com"  # 换一个网站测试也一样

    # 步骤一：自动发现Sitemap入口URL
    initial_sitemaps = discover_sitemap_urls(homepage, HEADERS)

    # 最终所有文章页的URL
    all_article_urls = set()

    # 待处理的Sitemap队列 (用于递归)
    to_process_queue = initial_sitemaps

    # 已处理过的Sitemap (防止因重定向导致的无限循环)
    processed_sitemaps = set()

    # 步骤二：循环处理Sitemap队列
    while to_process_queue:
        # 从队列中取出一个URL
        sitemap_url = to_process_queue.pop(0)

        if sitemap_url in processed_sitemaps:
            continue
        processed_sitemaps.add(sitemap_url)

        print(f"\n--- 正在处理Sitemap: {sitemap_url} ---")

        # 抓取Sitemap的XML内容
        xml_content = get_content(sitemap_url, HEADERS)
        if not xml_content:
            print("    抓取失败，跳过。")
            continue

        # 步骤三：解析与回退 (您要的核心)
        try:
            # 方案 A: 优先尝试 USP 库 (简单、自动)
            print("    尝试使用 [ultimate-sitemap-parser] 库解析...")

            parsed_sitemap = sitemap_from_str(xml_content.decode('utf-8', errors='ignore'))

            # 1. 提取页面 (all_pages 会自动处理 <urlset>)
            pages_found = 0
            for page in parsed_sitemap.all_pages():
                all_article_urls.add(page.url)
                pages_found += 1

            # 2. 提取子Sitemap (all_sub_sitemaps 会自动处理 <sitemapindex>)
            subs_found = 0
            for sub_sitemap in parsed_sitemap.all_sub_sitemaps():
                to_process_queue.append(sub_sitemap.url)
                subs_found += 1

            print(f"    [USP 成功] 发现 {pages_found} 个页面 和 {subs_found} 个子Sitemap。")

        except Exception as e:
            # 方案 B: USP 库解析失败 (例如人民网的索引)，执行手动回退
            print(f"    [USP 失败] 库解析出错: {e}")
            print("    --> 启动 [手动 ElementTree] 回退方案...")

            try:
                root = ET.fromstring(xml_content)

                # 手动检查是 <sitemapindex> 还是 <urlset>

                # 1. 手动解析索引文件 (<sitemap>)
                index_nodes = root.findall('ns:sitemap', NAMESPACES)
                if index_nodes:
                    subs_found = 0
                    for node in index_nodes:
                        loc = node.find('ns:loc', NAMESPACES)
                        if loc is not None and loc.text:
                            to_process_queue.append(loc.text)  # 添加回队列
                            subs_found += 1
                    print(f"    [手动回退] 发现 {subs_found} 个子Sitemap。")

                # 2. 手动解析页面文件 (<url>)
                url_nodes = root.findall('ns:url', NAMESPACES)
                if url_nodes:
                    pages_found = 0
                    for node in url_nodes:
                        loc = node.find('ns:loc', NAMESPACES)
                        if loc is not None and loc.text:
                            all_article_urls.add(loc.text)
                            pages_found += 1
                    print(f"    [手动回退] 发现 {pages_found} 个页面。")

                if not index_nodes and not url_nodes:
                    print("    [手动回退] 失败: XML中未找到 <sitemap> 或 <url> 标签。")

            except ET.ParseError as xml_e:
                print(f"    [手动回退] 失败: 无法解析XML。 错误: {xml_e}")

    # --- 4. 最终结果 ---
    print(f"\n==========================================")
    print(f"Sitemap 发现与解析全部完成。")
    print(f"共找到 {len(all_article_urls)} 个独立页面。")

    # 打印前50个看看
    print("--- 抽样展示前 50 个URL ---")
    article_list = list(all_article_urls)
    for url in article_list[:50]:
        print(url)


if __name__ == "__main__":
    main()