from typing import Optional

from CrawlerServiceEngine import ServiceContext
from CrawlTasks.crawler_config_tass import CRAWLER_CONFIG
from Workflow.IntelligenceCrawlFlow import CommonIntelligenceCrawlFlow

NAME = 'tass'
FLOW: Optional[CommonIntelligenceCrawlFlow] = None


def module_init(service_context: ServiceContext):
    global FLOW
    FLOW = CommonIntelligenceCrawlFlow(NAME, service_context)


def start_task(stop_event):
    if FLOW: FLOW.run_common_flow(CRAWLER_CONFIG.copy(), stop_event)
