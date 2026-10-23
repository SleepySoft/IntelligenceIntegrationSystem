"""i24NEWS 抓取任务入口。"""

from typing import Optional

from CrawlerServiceEngine import ServiceContext
from Workflow.IntelligenceCrawlFlow import CommonIntelligenceCrawlFlow
from crawler_config_i24news import CRAWLER_CONFIG


NAME = "i24news"
FLOW: Optional[CommonIntelligenceCrawlFlow] = None


def module_init(service_context: ServiceContext):
    global FLOW
    FLOW = CommonIntelligenceCrawlFlow(NAME, service_context)


def start_task(stop_event):
    if FLOW:
        FLOW.run_common_flow(CRAWLER_CONFIG.copy(), stop_event)
