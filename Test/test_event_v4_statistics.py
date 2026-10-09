from datetime import datetime, timezone

from ServiceComponent.IntelligenceStatisticsEngine import IntelligenceStatisticsEngine


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


def test_statistics_engine_uses_v4_archive_fields():
    collection = FakeCollection()
    engine = IntelligenceStatisticsEngine(collection)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 1, tzinfo=timezone.utc)

    engine.get_score_distribution(start, end)
    engine.get_daily_stats(start, end)
    engine.get_stats_summary(start, end)

    score_match = collection.pipelines[0][0]["$match"]
    assert set(score_match) == {"archived_at", "total_score"}
    assert collection.pipelines[1][1]["$group"]["_id"]["day"]["$dayOfMonth"]["date"] == "$archived_at"
    assert "archived_at" in collection.filters[0]
    assert collection.pipelines[2][1]["$group"]["_id"] == "$informant"
