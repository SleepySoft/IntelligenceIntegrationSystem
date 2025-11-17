import os
import asyncio
from playwright.async_api import async_playwright, Page, Browser, Playwright, BrowserContext
from typing import Dict, Optional

# --- 1. 可配置的选择器 (Configuration) ---
GEMINI_SELECTORS = {
    "input_box": '[aria-label="在此处输入提示"]',
    "send_button": '[aria-label="发送"]',
    "indicator": '[aria-label="发送"][disabled]',
    "response_content": ".markdown.markdown-main-panel:last-child"
}

# --- 用户数据目录 (用于保存登录状态) ---
# 将 'my_chrome_data' 替换为您想用的任何文件夹名
USER_DATA_DIR = os.path.join(os.getcwd(), "my_chrome_data")


class WebAIClient:
    """
    一个基于 Playwright 的异步 Web AI 对话客户端。
    使用持久化上下文直接操作本地 Chrome，可保存登录状态。
    """

    def __init__(self, url: str, selectors: Dict[str, str]):
        self.url = url
        self.selectors = selectors
        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def start(self, user_data_dir: str, headless: bool = False,
                    proxy: Optional[Dict[str, str]] = None) -> None:  # <<< MODIFIED
        """
        启动持久化上下文的浏览器，加载或创建用户数据，并处理首次登录。
        :param user_data_dir: 用于存储浏览器会话（Cookie、登录状态等）的目录。
        :param headless: 是否以无头模式运行。首次登录必须为 False。
        :param proxy: 代理服务器配置字典。
        """
        print(f"正在使用用户数据目录启动浏览器: {user_data_dir}")
        self.playwright = await async_playwright().start()

        self.context = await self.playwright.chromium.launch_persistent_context(  # <<< MODIFIED
            user_data_dir=user_data_dir,
            headless=headless,
            proxy=proxy,
            channel="chrome",  # <<< 关键：指定使用已安装的 Chrome 浏览器
            args=['--start-maximized']  # 可选：最大化窗口
        )

        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

        print(f"正在导航至: {self.url}")
        await self.page.goto(self.url, timeout=90000, wait_until="domcontentloaded")

        # <<< MODIFIED: 关键修改 - 优化提示并增加智能等待 ---
        print("-" * 30)
        print("浏览器已启动。")
        print("如果是首次运行，请在该窗口中手动登录。")
        print("!!! 重要: 登录后请不要关闭此浏览器窗口，脚本将自动接管。")
        print("正在等待页面加载完成（标志：出现输入框）...")

        try:
            # 等待输入框出现，作为页面已完全准备好的信号
            input_box_locator = self.page.locator(self.selectors["input_box"])
            await input_box_locator.wait_for(timeout=90000)
            print("页面准备就绪，脚本继续执行。")
        except Exception as e:
            print("等待页面加载超时或失败，请检查选择器或网络。")
            raise e
        finally:
            print("-" * 30)
        # --- End of MODIFIED section ---

    async def ask(self, prompt: str, response_timeout_sec: int = 180) -> str:
        if not self.page:
            raise ConnectionError("客户端未启动，请先调用 start() 方法。")
        try:
            print(f"正在输入提示: {prompt[:50]}...")
            await self.page.locator(self.selectors["input_box"]).fill(prompt)
            await self.page.locator(self.selectors["send_button"]).click()
            print("已发送，正在等待回复...")
            indicator_selector = self.selectors["indicator"]
            await self.page.wait_for_selector(
                indicator_selector,
                state='detached',
                timeout=response_timeout_sec * 1000
            )
            print("回复已生成，正在提取内容...")
            response_text = await self.page.locator(self.selectors["response_content"]).inner_text()
            return response_text.strip()
        except Exception as e:
            print(f"在对话过程中发生错误: {e}")
            return f"错误: {e}"

    async def close(self) -> None:  # <<< MODIFIED
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()
        print("浏览器已关闭。")


class SyncWebAIClient:
    """
    一个同步的 Web AI 对话客户端封装。
    """

    def __init__(self, url: str, selectors: Dict[str, str], user_data_dir: str, headless: bool = False,
                 proxy: Optional[Dict[str, str]] = None):  # <<< MODIFIED
        self.async_client = WebAIClient(url, selectors)
        print("正在初始化同步客户端...")
        try:
            asyncio.run(
                self.async_client.start(user_data_dir=user_data_dir, headless=headless, proxy=proxy))  # <<< MODIFIED
        except Exception as e:
            print(f"启动失败: {e}")
            raise

    def ask(self, prompt: str) -> str:
        return asyncio.run(self.async_client.ask(prompt))

    def close(self) -> None:
        asyncio.run(self.async_client.close())


# --- 如何使用 ---
def main_sync(user_data_dir: str, proxy: Optional[Dict[str, str]] = None):  # <<< MODIFIED
    print("\n--- 同步客户端测试 ---")
    client = None
    try:
        client = SyncWebAIClient(
            url="https://gemini.google.com/",
            selectors=GEMINI_SELECTORS,
            user_data_dir=user_data_dir,  # <<< MODIFIED
            headless=False,  # 首次登录必须为 False
            proxy=proxy
        )
        response = client.ask("你好，现在登录应该没有问题了吧？")
        print("\nAI 回答:\n", response)
    finally:
        if client:
            client.close()

if __name__ == "__main__":
    # --- 在这里定义你的代理 ---
    # 如果不需要代理，将其设置为 None
    PROXY_CONFIG = {
        "server": "socks5://127.0.0.1:10808",  # 示例 SOCKS5 代理
        # "server": "http://127.0.0.1:8888",  # 示例 HTTP 代理
        # "username": "user",
        # "password": "pass"
    }
    # PROXY_CONFIG = None # 如果不需要代理

    # 首次运行时，会打开一个Chrome窗口，请在该窗口中登录您的Google账户。
    # 登录成功后，关闭浏览器即可。数据会保存在 'my_chrome_data' 文件夹。
    # 从第二次运行开始，它会直接使用保存好的登录状态，跳过登录页面！
    main_sync(user_data_dir=USER_DATA_DIR, proxy=PROXY_CONFIG)

    # 异步方式运行
    # asyncio.run(main_async(proxy=PROXY_CONFIG))