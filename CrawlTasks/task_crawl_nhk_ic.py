from functools import partial
from playwright.sync_api import Page
from CrawlerServiceEngine import ServiceContext
from MyPythonUtility.easy_config import EasyConfig
from Workflow.CommonFlowUtility import CrawlContext
from CrawlTasks.crawler_config_nhk import CRAWLER_CONFIG
from IntelligenceCrawler.CrawlPipeline import run_pipeline
from Workflow.CommonFeedsCrawFlow import build_crawl_ctx_by_service_ctx
from Workflow.IntelligenceCrawlFlow import intelligence_crawler_fileter, intelligence_crawler_result_handler

NAME = 'nhk'
config: EasyConfig | None = None
crawl_context: CrawlContext | None = None


def module_init(service_context: ServiceContext):
    global config
    global crawl_context
    config = service_context.config
    crawl_context = build_crawl_ctx_by_service_ctx(NAME, service_context)


def start_task(stop_event):
    local_config = CRAWLER_CONFIG.copy()

    # Override generated config.
    local_config['d_fetcher_init_param']['proxy'] = 'http://127.0.0.1:10809'
    local_config['e_fetcher_init_param']['proxy'] = 'http://127.0.0.1:10809'
    local_config['e_fetcher_kwargs']['post_extra_action'] = [
        {"text": "内容について確認しました",         "action": "click", "timeout": 3000},
        {"text": "次へ",                         "action": "click", "timeout": 3000},
        {"text": "サービスの利用を開始する",         "action": "click", "timeout": 1000},
        {"text": "確認しました / I understand",   "action": "click", "timeout": 1000},
    ]
    local_config['article_filter'] = partial(intelligence_crawler_fileter, context=crawl_context)
    local_config['content_handler'] = partial(intelligence_crawler_result_handler, context=crawl_context)

    run_pipeline(local_config, crawler_governor=crawl_context.crawler_governor)

    # Check and submit cached data.
    crawl_context.submit_cached_data(10)
    # Randomly delay for next crawl.
    # CrawlContext.wait_interruptibly(random.randint(10, 15) * 60, stop_event)
    crawl_context.crawler_governor.wait_interval(60 * 15, stop_event=stop_event)
