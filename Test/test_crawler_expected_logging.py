import logging
from types import SimpleNamespace

import pytest

from IntelligenceCrawler.CrawlPipeline import summarize_expected_crawl_exception
from IntelligenceCrawler.CrawlerGovernanceCore import CrawlSession
from IntelligenceCrawler.Extractor import ExtractionResult
from ServiceComponent.IntelligenceHubDefines_v4 import CollectedData
from Tools.RSSFetcher import fetch_feed
from Workflow.CommonFlowUtility import CrawlContext
from Workflow.IntelligenceCrawlFlow import intelligence_crawler_result_handler


def _collected() -> CollectedData:
    return CollectedData(
        UUID='crawler-log-test',
        token='-',
        title='Title',
        content='Long enough content.',
        informant='https://example.test/article',
    )


def test_hub_rejection_is_a_summarized_skip_without_traceback(caplog):
    logger = logging.getLogger('test.crawler.expected')
    context = CrawlContext(
        'test', 'http://127.0.0.1:5000', 'token', None, logger=logger,
    )
    context._submit_collected_data = lambda *_args: {
        'resp': 'rejected', 'reason': 'stale_content',
    }

    with caplog.at_level(logging.INFO, logger=logger.name):
        with pytest.raises(CrawlSession.Skip, match='stale_content'):
            context.submit_collected_data('news/world', _collected())

    records = [
        record for record in caplog.records
        if record.name == logger.name and 'category=hub_rejected' in record.getMessage()
    ]
    assert len(records) == 1
    assert 'category=hub_rejected' in records[0].getMessage()
    assert 'reason=stale_content' in records[0].getMessage()
    assert records[0].exc_info is None


def test_invalid_collected_data_is_a_summarized_skip():
    warnings = []
    context = SimpleNamespace(
        logger=SimpleNamespace(warning=warnings.append),
        submit_collected_data=lambda *_args: None,
    )
    result = ExtractionResult(markdown_content='', metadata={'title': ''})

    with pytest.raises(CrawlSession.Skip, match='invalid_collected_data'):
        intelligence_crawler_result_handler(
            'https://example.test/invalid', 'news/world', result, context,
        )

    assert len(warnings) == 1
    assert 'category=invalid_collected_data' in warnings[0]
    assert 'title:string_too_short' in warnings[0]
    assert 'content:string_too_short' in warnings[0]


def test_empty_feed_logs_one_summary_without_traceback(caplog):
    logger_name = 'Tools.RSSFetcher'
    with caplog.at_level(logging.WARNING, logger=logger_name):
        result = fetch_feed(
            'https://example.test/feed.xml',
            lambda *_args, **_kwargs: {'content': '', 'errors': []},
        )

    records = [record for record in caplog.records if record.name == logger_name]
    assert result.fatal is True
    assert result.errors == ['Empty feed content']
    assert len(records) == 1
    assert 'category=empty_content' in records[0].getMessage()
    assert records[0].exc_info is None


def test_navigation_race_has_stable_summary_but_unknown_error_does_not():
    expected = RuntimeError(
        'Page.content: Unable to retrieve content because the page is navigating '
        'and changing the content.'
    )

    assert summarize_expected_crawl_exception(expected) == (
        'page_navigation_in_progress',
        'rendered content requested while the page was still navigating',
    )
    assert summarize_expected_crawl_exception(RuntimeError('unknown')) is None
