from typing import Optional

from CrawlerServiceEngine import ServiceContext
from crawler_config_techcrunch_startups import CRAWLER_CONFIG
from Workflow.IntelligenceCrawlFlow import CommonIntelligenceCrawlFlow


NAME = "techcrunch_startups"
FLOW: Optional[CommonIntelligenceCrawlFlow] = None


def module_init(service_context: ServiceContext):
    global FLOW
    FLOW = CommonIntelligenceCrawlFlow(NAME, service_context)


def start_task(stop_event):
    if FLOW:
        FLOW.run_common_flow(CRAWLER_CONFIG.copy(), stop_event)
