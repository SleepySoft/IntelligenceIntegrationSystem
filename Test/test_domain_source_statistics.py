from datetime import datetime, timezone
from types import SimpleNamespace

from IntelligenceHubWebService import IntelligenceHubWebService


class FakeStatisticsEngine:
    def __init__(self, marker):
        self.marker = marker
        self.calls = []

    def get_hourly_source_submissions(self, start, end, *, top_n=None):
        self.calls.append((start, end, top_n))
        return {
            "sources": [f"{self.marker}.example"],
            "source_count": 1,
            "total_count": 2,
        }


class FakeDomainResources:
    def __init__(self, domains):
        self.domains = domains

    def enabled_domains(self):
        return list(self.domains)


def test_source_statistics_keep_each_domain_in_an_independent_group():
    news_engine = FakeStatisticsEngine("news")
    industry_engine = FakeStatisticsEngine("industry")
    service = object.__new__(IntelligenceHubWebService)
    service.domain_resources = FakeDomainResources([
        SimpleNamespace(
            name="news", display_name="国际新闻", is_default=True,
            statistics_engine=news_engine,
        ),
        SimpleNamespace(
            name="industry", display_name="行业情报", is_default=False,
            statistics_engine=industry_engine,
        ),
    ])
    start = datetime(2026, 10, 27, tzinfo=timezone.utc)
    end = datetime(2026, 10, 28, tzinfo=timezone.utc)

    payload = service._group_source_statistics(
        "get_hourly_source_submissions", start, end, top_n=20)

    assert payload["domain_count"] == 2
    assert [group["domain_name"] for group in payload["domains"]] == [
        "news", "industry"]
    assert payload["domains"][0]["statistics"]["sources"] == ["news.example"]
    assert payload["domains"][1]["statistics"]["sources"] == [
        "industry.example"]
    assert "sources" not in payload
    assert news_engine.calls == [(start, end, 20)]
    assert industry_engine.calls == [(start, end, 20)]


def test_source_statistics_skip_domains_without_statistics_engine():
    service = object.__new__(IntelligenceHubWebService)
    service.domain_resources = FakeDomainResources([
        SimpleNamespace(
            name="debug", display_name="调试", is_default=False,
            statistics_engine=None,
        ),
    ])

    payload = service._group_source_statistics(
        "get_hourly_source_submissions",
        datetime(2026, 10, 27, tzinfo=timezone.utc),
        datetime(2026, 10, 28, tzinfo=timezone.utc),
    )

    assert payload["domain_count"] == 0
    assert payload["domains"] == []
