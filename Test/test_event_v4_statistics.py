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
