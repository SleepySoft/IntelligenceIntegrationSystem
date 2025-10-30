import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ------------------- 配置区域 -------------------

# AI 对话网页的 URL
# 请替换为你需要访问的实际网址，例如 "https://gemini.google.com/"
AI_CHAT_URL = "https://gemini.google.com/"

# ChromeDriver 的路径
# 如果你已经将 chromedriver 添加到了系统 PATH，可以将此项设为 None
# 否则，请提供 chromedriver.exe 的完整路径，例如: r"C:\path\to\your\chromedriver.exe"
# 注意：Windows 路径前的 'r' 是为了防止转义字符问题。
CHROME_DRIVER_PATH = r'C:\D\chromedriver-win64\chromedriver.exe'

# 页面元素的选择器 (Selectors)
# 这些是用来定位页面上各个元素的 CSS 选择器
GEMINI_SELECTORS = {
    "input_box": '[aria-label="在此处输入提示"]',  # 输入框
    "send_button": 'button[aria-label="发送"]',  # 发送按钮
    "thinking_indicator": 'button[aria-label="发送"][disabled]',  # AI 思考中的指示器（发送按钮被禁用）
    "response_content": ".markdown.markdown-main-panel"  # 回复内容的容器
}


class AIChatBot:
    """
    一个使用 Selenium 操作 AI 对话网页的机器人。
    """

    def __init__(self, driver_path=None):
        """
        初始化浏览器驱动。
        :param driver_path: ChromeDriver 的路径。如果为 None，则 Selenium 会尝试从系统 PATH 中寻找。
        """
        options = Options()
        options.binary_location = r"C:\D\chrome-win64\chrome.exe"

        if driver_path:
            service = Service(executable_path=driver_path)
            self.driver = webdriver.Chrome(service=service, options=options)
        else:
            # 自动管理驱动，更推荐
            self.driver = webdriver.Chrome(options=options)

            # 创建一个 WebDriverWait 对象，用于等待元素加载，超时时间设置为 30 秒
        self.wait = WebDriverWait(self.driver, 30)
        print("浏览器已启动。")

    def open_chat_page(self, url):
        """
        打开指定的 AI 对话网页。
        """
        print(f"正在打开网页: {url}")
        self.driver.get(url)
        # 等待输入框加载完成，以确认页面已准备好
        try:
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, GEMINI_SELECTORS["input_box"]))
            )
            print("网页加载成功，输入框已找到。")
        except TimeoutException:
            print("错误：页面加载超时或找不到输入框。请检查 URL 和选择器是否正确。")
            self.close()

    def send_prompt(self, prompt_text):
        """
        在输入框中输入文本并点击发送按钮。
        :param prompt_text: 要发送给 AI 的问题或提示。
        """
        try:
            # 1. 定位输入框
            input_box = self.driver.find_element(By.CSS_SELECTOR, GEMINI_SELECTORS["input_box"])

            # 2. 清空输入框（可选，防止有默认内容）并输入文本
            input_box.clear()
            input_box.send_keys(prompt_text)

            # 3. 定位并点击发送按钮
            send_button = self.driver.find_element(By.CSS_SELECTOR, GEMINI_SELECTORS["send_button"])
            send_button.click()

            print(f"已发送提示: '{prompt_text}'")
            return True
        except NoSuchElementException:
            print("错误：找不到输入框或发送按钮。")
            return False
        except Exception as e:
            print(f"发送提示时发生未知错误: {e}")
            return False

    def get_response(self):
        """
        等待 AI 回复完成，并提取最新的回复内容。
        :return: AI 的回复文本，如果失败则返回 None。
        """
        try:
            print("正在等待 AI 回复...")

            # 1. 等待 AI 开始处理（发送按钮变为禁用状态）
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, GEMINI_SELECTORS["thinking_indicator"]))
            )
            print("AI 正在处理请求...")

            # 2. 等待 AI 处理完成（发送按钮恢复可用状态）
            #    这里的逻辑是等待 "disabled" 属性消失，也就是等待元素不再是那个指示器
            self.wait.until_not(
                EC.presence_of_element_located((By.CSS_SELECTOR, GEMINI_SELECTORS["thinking_indicator"]))
            )
            print("AI 回复已生成。")

            # 3. 提取所有回复，并返回最后一个
            #    因为页面上可能有多次对话，所以用 find_elements 获取所有回复
            time.sleep(1)  # 短暂等待，确保 DOM 完全更新
            response_elements = self.driver.find_elements(By.CSS_SELECTOR, GEMINI_SELECTORS["response_content"])
            if response_elements:
                latest_response = response_elements[-1]  # 获取最后一个元素，即最新的回复
                return latest_response.text
            else:
                print("错误：找不到任何回复内容。")
                return None

        except TimeoutException:
            print("错误：等待 AI 回复超时。")
            return None
        except Exception as e:
            print(f"获取回复时发生未知错误: {e}")
            return None

    def close(self):
        """
        关闭浏览器。
        """
        if self.driver:
            self.driver.quit()
            print("浏览器已关闭。")


# ------------------- 主程序入口 -------------------
if __name__ == "__main__":
    # 实例化机器人
    bot = AIChatBot(driver_path=CHROME_DRIVER_PATH)

    # 打开网页
    bot.open_chat_page(AI_CHAT_URL)

    # 准备要发送的问题
    my_prompt = "你好，请用中文介绍一下什么是人工智能？"

    # 发送问题
    if bot.send_prompt(my_prompt):
        # 获取并打印回复
        response = bot.get_response()
        if response:
            print("\n----------- AI 回复 -----------")
            print(response)
            print("------------------------------\n")

    # 你可以继续进行下一次对话
    my_prompt_2 = "它有哪些主要的应用领域？"
    if bot.send_prompt(my_prompt_2):
        response_2 = bot.get_response()
        if response_2:
            print("\n----------- AI 回复 2 -----------")
            print(response_2)
            print("--------------------------------\n")

    # 任务完成后关闭浏览器
    bot.close()
