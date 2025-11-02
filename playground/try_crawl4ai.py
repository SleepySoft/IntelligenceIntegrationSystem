import asyncio
from crawl4ai import AsyncWebCrawler

# pip install crawl4ai
# playwright install --with-deps chromium # 安装浏览器依赖

# 定义一个异步函数
async def main():
    url = "https://example-dynamic-website.com/article/123"

    # 初始化爬虫
    crawler = AsyncWebCrawler()

    # 运行爬虫
    # 它会自动使用无头浏览器 (Playwright) 来渲染页面
    # 然后提取主要内容，并将其转换为Markdown
    result = await crawler.arun(url=url)

    # result.markdown 包含了提取出的干净正文 (Markdown格式)
    if result.markdown:
        print("--- 提取内容 (Markdown) ---")
        print(result.markdown)

        # 也可以获取元数据 (如果提取到)
        if result.metadata:
            print("\n--- 元数据 ---")
            print(f"标题: {result.metadata.get('title')}")
            print(f"作者: {result.metadata.get('author')}")
            print(f"日期: {result.metadata.get('date')}")
            # print(result.metadata) # 查看所有元数据
    else:
        print("未能提取内容。")


# 运行异步主函数
if __name__ == "__main__":
    asyncio.run(main())