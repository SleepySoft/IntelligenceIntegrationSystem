from datetime import datetime, timezone

from ServiceComponent.IntelligenceStatisticsEngine import IntelligenceStatisticsEngine
from ServiceComponent.intelligence_contracts import (
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TIME_GOT,
    APPENDIX_TOTAL_SCORE,
    appendix_path,
)


class FakeCollection:
    def __init__(self):
        self.pipelines = []
        self.filters = []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return []

    def count_documents(self, query):
        self.filters.append(query)
        return 0


def test_statistics_engine_does_not_treat_pymongo_collection_attribute_as_wrapper():
    class MongoLikeCollection(FakeCollection):
        def __init__(self):
            super().__init__()
            self.collection = FakeCollection()

    collection = MongoLikeCollection()
    engine = IntelligenceStatisticsEngine(collection)
    engine.get_daily_stats(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    assert len(collection.pipelines) == 1
    assert collection.collection.pipelines == []


def test_statistics_engine_uses_v4_archive_fields():
    collection = FakeCollection()
    engine = IntelligenceStatisticsEngine(collection)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 1, tzinfo=timezone.utc)

    engine.get_score_distribution(start, end)
    engine.get_daily_stats(start, end)
    engine.get_stats_summary(start, end)

    score_match = collection.pipelines[0][0]["$match"]
    assert set(score_match) == {appendix_path(APPENDIX_TIME_ARCHIVED), appendix_path(APPENDIX_TOTAL_SCORE)}
    assert collection.pipelines[1][1]["$group"]["_id"]["day"]["$dayOfMonth"]["date"] == "$" + appendix_path(APPENDIX_TIME_ARCHIVED)
    assert appendix_path(APPENDIX_TIME_ARCHIVED) in collection.filters[0]
    assert collection.pipelines[2][1]["$group"]["_id"] == "$INFORMANT"


def test_score_distribution_buckets_decimal_scores():
    class ScoreCollection(FakeCollection):
        def aggregate(self, pipeline):
            self.pipelines.append(pipeline)
            return [
                {"_id": 4.4, "count": 3},
                {"_id": 4.6, "count": 2},
                {"_id": 9.3, "count": 1},
            ]

    result = IntelligenceStatisticsEngine(ScoreCollection()).get_score_distribution(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    assert result["4"] == 3
    assert result["5"] == 2
    assert result["9"] == 1
    assert sum(result.values()) == 6


def test_hourly_source_submissions_groups_urls_by_domain_and_fills_empty_hours():
    class SourceCollection(FakeCollection):
        def aggregate(self, pipeline):
            self.pipelines.append(pipeline)
            return [
                {"_id": {"year": 2026, "month": 1, "day": 1, "hour": 8,
                         "informant": "https://www.example.com/a"}, "count": 2},
                {"_id": {"year": 2026, "month": 1, "day": 1, "hour": 8,
                         "informant": "https://example.com/b"}, "count": 1},
                {"_id": {"year": 2026, "month": 1, "day": 1, "hour": 10,
                         "informant": "https://news.test.org/c"}, "count": 4},
            ]

    engine = IntelligenceStatisticsEngine(SourceCollection())
    engine._IntelligenceStatisticsEngine__local_timezone = "UTC"
    result = engine.get_hourly_source_submissions(
        datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
    )

    assert result["slots"] == [
        "2026-01-01 08:00", "2026-01-01 09:00", "2026-01-01 10:00"]
    assert result["series"]["example.com"] == [3, 0, 0]
    assert result["series"]["news.test.org"] == [0, 0, 4]
    assert "其他" not in result["series"]
    assert result["source_count"] == 2
    assert result["total_count"] == 7
    match = engine._IntelligenceStatisticsEngine__collection.pipelines[0][0]["$match"]
    assert appendix_path(APPENDIX_TIME_GOT) in match


def test_hourly_source_submissions_uses_cache_as_accepted_intake_source():
    archive = FakeCollection()
    cache = FakeCollection()
    engine = IntelligenceStatisticsEngine(archive, cache_collection=cache)
    engine._IntelligenceStatisticsEngine__local_timezone = "UTC"

    result = engine.get_hourly_source_submissions(
        datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
    )

    assert archive.pipelines == []
    assert cache.pipelines[0][0]["$match"]["collect_time"]
    assert result["count_basis"] == "accepted_intake"


def test_source_quality_combines_states_low_value_reasons_and_rejections():
    class CacheCollection(FakeCollection):
        def aggregate(self, pipeline):
            self.pipelines.append(pipeline)
            return [
                {"_id": {"informant": "https://www.example.com/a", "state": "A"},
                 "count": 4, "last_submission": datetime(2026, 1, 2, tzinfo=timezone.utc)},
                {"_id": {"informant": "https://example.com/b", "state": "D"},
                 "count": 3, "last_submission": datetime(2026, 1, 3, tzinfo=timezone.utc)},
                {"_id": {"informant": "https://example.com/c", "state": "R"},
                 "count": 1, "last_submission": datetime(2026, 1, 3, tzinfo=timezone.utc)},
            ]

    class LowCollection(FakeCollection):
        def aggregate(self, pipeline):
            self.pipelines.append(pipeline)
            return [
                {"_id": {"informant": "https://example.com/b",
                         "reason": "anti_bot_or_access_denied"}, "count": 2},
                {"_id": {"informant": "https://example.com/b", "reason": "sports"},
                 "count": 1},
            ]

    class RejectedCollection(FakeCollection):
        def aggregate(self, pipeline):
            self.pipelines.append(pipeline)
            return [{"_id": "https://example.com/rejected", "count": 2}]

    result = IntelligenceStatisticsEngine(
        FakeCollection(), cache_collection=CacheCollection(),
        low_value_collection=LowCollection(),
        intake_rejected_collection=RejectedCollection(),
    ).get_source_quality(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 4, tzinfo=timezone.utc),
    )

    assert result["source_count"] == 1
    row = result["sources"][0]
    assert row["source"] == "example.com"
    assert row["accepted"] == 8
    assert row["archived"] == 4
    assert row["low_value"] == 3
    assert row["content_defect"] == 2
    assert row["retrying"] == 1
    assert row["intake_rejected"] == 2
    assert row["status"] == "attention"
    assert row["low_value_reasons"]["sports"] == 1
