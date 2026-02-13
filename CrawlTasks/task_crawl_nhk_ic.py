from typing import Optional

from CrawlerServiceEngine import ServiceContext
from CrawlTasks.crawler_config_nhk import CRAWLER_CONFIG
from Workflow.IntelligenceCrawlFlow import CommonIntelligenceCrawlFlow


NAME = 'nhk'
FLOW: Optional[CommonIntelligenceCrawlFlow] = None


def module_init(service_context: ServiceContext):
    global FLOW
    FLOW = CommonIntelligenceCrawlFlow(NAME, service_context)


def start_task(stop_event):
    local_crawler_config = CRAWLER_CONFIG.copy()
    
    # Manually add extra post action to click web's button.
    local_crawler_config['e_fetcher_kwargs']['post_extra_action'] = [
        {"text": "確認しました / I understand",   "action": "click", "timeout": 3000},
        {"text": "内容について確認しました",         "action": "click", "timeout": 3000},
        {"text": "次へ",                         "action": "click", "timeout": 3000},
        {"text": "サービスの利用を開始する",         "action": "click", "timeout": 1000},
    ]

    if FLOW: FLOW.run_common_flow(local_crawler_config, stop_event)
