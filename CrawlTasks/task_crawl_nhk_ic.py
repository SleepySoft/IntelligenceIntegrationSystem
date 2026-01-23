from playwright.sync_api import Page
from CrawlerServiceEngine import ServiceContext
from MyPythonUtility.easy_config import EasyConfig
from Workflow.CommonFlowUtility import CrawlContext
from CrawlTasks.crawler_config_nhk import CRAWLER_CONFIG
from IntelligenceCrawler.CrawlPipeline import run_pipeline
from Workflow.CommonFeedsCrawFlow import build_crawl_ctx_by_service_ctx


NAME = 'nhk'
config: EasyConfig | None = None
crawl_context: CrawlContext | None = None


# 同时兼容两种确认方式（或许是受网络影响，网页弹出的确认界面可能不一样）。

def conditional_click_nhk(page: Page):
    """
    Enhanced function to handle multiple popup variants on NHK website.
    Returns True if popup was found and processed, False otherwise.
    """
    # Define all text constants
    CHECKBOX_TEXT = "内容について確認しました"
    NEXT_BUTTON_TEXT = "次へ"
    START_SERVICE_TEXT = "サービスの利用を開始する"
    DIRECT_BUTTON_TEXT = "確認しました / I understand"

    def click_button(button_text, description="Button"):
        """通用点击按钮函数"""
        try:
            button_locator = page.get_by_text(button_text, exact=True)
            if button_locator.is_visible(timeout=3000):
                button_locator.click()
                print(f"{description} '{button_text}' clicked successfully.")
                page.wait_for_timeout(1000)  # 等待操作完成
                return True
        except Exception as e:
            print(f"{description} '{button_text}' not found or click failed: {str(e)}")
        return False

    def click_checkbox():
        """点击复选框函数"""
        try:
            checkbox_locator = page.get_by_text(CHECKBOX_TEXT, exact=True)
            if checkbox_locator.is_visible(timeout=3000):
                checkbox_locator.click()
                print("Checkbox clicked successfully.")
                page.wait_for_timeout(500)  # 短暂等待状态更新
                return True
        except Exception as e:
            print(f"Checkbox not found or click failed: {str(e)}")
        return False

    def is_button_enabled(button_text):
        """检查按钮是否可用"""
        try:
            button_locator = page.get_by_text(button_text, exact=True)
            is_disabled = button_locator.get_attribute("disabled")
            return is_disabled is None
        except:
            return False

    try:
        # Variant 1: Checkbox + Button pattern (new style with multiple steps)
        try:
            # 检查是否有 Variant 1 的复选框
            checkbox_locator = page.get_by_text(CHECKBOX_TEXT, exact=True)
            if checkbox_locator.is_visible(timeout=3000):
                print("Detected Variant 1 popup (checkbox + button), processing...")

                # Step 1: 点击复选框
                if not click_checkbox():
                    return False

                # 等待按钮状态更新
                page.wait_for_timeout(1500)

                # Step 2: 点击"次へ"按钮
                if is_button_enabled(NEXT_BUTTON_TEXT):
                    if click_button(NEXT_BUTTON_TEXT, "Next button"):
                        # 等待可能的页面变化
                        page.wait_for_timeout(2000)

                        # Step 3: 尝试点击新增的"サービスの利用を開始する"按钮
                        if click_button(START_SERVICE_TEXT, "Start service button"):
                            print("Start service button handled successfully.")
                        else:
                            print("Start service button not found, continuing...")

                        print("Variant 1 popup handled successfully.")
                        return True
                    else:
                        print("Failed to click next button.")
                        return False
                else:
                    print("Next button is disabled, cannot proceed.")
                    return False

        except Exception as e:
            print(f"Variant 1 not detected or failed: {str(e)}")

        # Variant 2: Direct button pattern (old style)
        try:
            if click_button(DIRECT_BUTTON_TEXT, "Direct confirmation button"):
                print("Variant 2 popup handled successfully.")
                return True
        except Exception as e:
            print(f"Variant 2 not detected or failed: {str(e)}")

        # 如果没有检测到已知弹窗变体
        print("No known popup variant detected, continuing with scraping.")
        return False

    except Exception as e:
        print(f"Unexpected error during popup handling: {str(e)}")
        return False


def module_init(service_context: ServiceContext):
    global config
    global crawl_context
    config = service_context.config
    crawl_context = build_crawl_ctx_by_service_ctx(NAME, service_context)


def start_task(stop_event):
    local_config = CRAWLER_CONFIG.copy()

    # Override generated config.
    local_config['d_fetcher_init_param']['proxy'] = ''
    local_config['e_fetcher_init_param']['proxy'] = ''
    local_config['e_fetcher_kwargs']['post_extra_action'] = conditional_click_nhk

    run_pipeline(local_config)

    # Check and submit cached data.
    crawl_context.submit_cached_data(10)
    # Randomly delay for next crawl.
    # CrawlContext.wait_interruptibly(random.randint(10, 15) * 60, stop_event)
    crawl_context.crawler_governor.wait_interval(60 * 15, stop_event=stop_event)

# --------------------------------------------- Manual Code End ---------------------------------------------
