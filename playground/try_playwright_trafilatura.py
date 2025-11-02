import asyncio
from playwright.async_api import async_playwright
import trafilatura

# pip install playwright trafilatura
# playwright install

async def fetch_and_extract(url):
    async with async_playwright() as p:
        # 启动浏览器
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            # 1. 抓取 (使用浏览器)
            await page.goto(url, wait_until='networkidle')
            # wait_until='networkidle' 会等待网络请求基本停止，
            # 这是一个好时机，说明动态内容很可能加载完了

            # 获取渲染后的完整HTML
            html_content = await page.content()

            # 2. 提取 (使用Trafilatura)
            # 你也可以传入 page.url 作为 URL 提示
            result = trafilatura.extract(
                html_content,
                include_metadata=True,
                output_format='json',
                url=url
            )

            return result

        except Exception as e:
            print(f"抓取或提取失败: {e}")
            return None
        finally:
            await browser.close()


# --- 运行 ---
async def main():
    url = "https://example-dynamic-website.com/article/123"

    data = await fetch_and_extract(url)

    if data:
        import json
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print("未获取到数据。")


if __name__ == "__main__":
    asyncio.run(main())