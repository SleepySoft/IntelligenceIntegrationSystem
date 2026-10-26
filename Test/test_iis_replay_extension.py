import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

from ServiceComponent.adapters import IISUnarchivedReplayExtension
from ServiceComponent.pipeline import ANALYSIS_REQUESTED
from ServiceComponent.runtime import HubRuntime
from ServiceComponent.intelligence_contracts import (
    APPENDIX_FIELD,
    APPENDIX_RETRY_FIRST_FAILED_AT,
    APPENDIX_RETRY_LEVEL,
    APPENDIX_RETRY_NEXT_AT,
)


class Collection:
    def __init__(self):
        self.records = [
            {"_id": "mongo-id-1", "UUID": "old-1", "informant": "https://example.test/1"},
            {"_id": "mongo-id-2", "UUID": "old-2", "informant": "https://example.test/2"},
        ]
        self.updates = []

    def find(self, query):
        if query.get("APPENDIX.__ARCHIVED__") == "R":
            return []
        assert query == {
            "$and": [
                {"__ARCHIVED__": {"$exists": False}},
                {"APPENDIX.__ARCHIVED__": {"$exists": False}},
            ]
        }
        return self.records

    def update_one(self, query, update):
        self.updates.append((query, update))
        return SimpleNamespace(modified_count=1)

    def update_many(self, query, update):
        return SimpleNamespace(modified_count=0)


class Cache:
    def __init__(self):
        self.collection = Collection()


class Context:
    name = "news"

    def __init__(self):
        self.mongo_db_cache = Cache()


class Registry:
    def __init__(self):
        self.context = Context()

    def enabled_domains(self):
        return [self.context]


def test_replay_extension_resumes_unarchived_cache_at_analysis_stage():
    runtime = HubRuntime()
    registry = Registry()
    extension = IISUnarchivedReplayExtension(registry)
    received = []
    runtime.install(extension)
    runtime.subscribe(ANALYSIS_REQUESTED, lambda event, _: received.append(event.payload))
    runtime.start()

    assert _wait_until(lambda: len(received) == 2)
    runtime.stop()
    assert [item["informant"] for item in received] == [
        "https://example.test/1", "https://example.test/2"]
    assert all(str(UUID(item["UUID"])) == item["UUID"] for item in received)
    assert len(registry.context.mongo_db_cache.collection.updates) == 2
    assert extension.replayed == 2


class RetryCollection(Collection):
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.records = [{
            "_id": "retry-id",
            "UUID": "30eb0459-998d-486f-8eac-470b9dfd98ec",
            "informant": "https://example.test/retry",
            APPENDIX_FIELD: {
                "__ARCHIVED__": "R",
                APPENDIX_RETRY_LEVEL: 1,
                APPENDIX_RETRY_FIRST_FAILED_AT: now - timedelta(hours=2),
                APPENDIX_RETRY_NEXT_AT: now - timedelta(minutes=1),
            },
        }]
        self.updates = []

    def find(self, query):
        if query.get("APPENDIX.__ARCHIVED__") == "R":
            return self.records
        return []


def test_retry_records_use_dedicated_lowest_priority_lane():
    registry = Registry()
    registry.context.mongo_db_cache.collection = RetryCollection()
    extension = IISUnarchivedReplayExtension(
        registry, poll_interval=60, batch_size=10)
    runtime = HubRuntime()
    received = []
    runtime.subscribe(ANALYSIS_REQUESTED, lambda event, _: received.append(event.payload))

    extension._replay_due_retries(runtime)
    assert runtime.stats["queued_by_lane"] == {"retry": 1}
    runtime.start()
    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()

    assert received[0]["informant"] == "https://example.test/retry"
    assert extension.retried == 1


def test_legacy_terminal_errors_are_migrated_once_to_r1():
    class LegacyCollection(Collection):
        def update_many(self, query, update):
            self.migration = (query, update)
            return SimpleNamespace(modified_count=4)

    registry = Registry()
    collection = LegacyCollection()
    registry.context.mongo_db_cache.collection = collection
    extension = IISUnarchivedReplayExtension(registry)

    extension._migrate_legacy_errors()

    query, update = collection.migration
    assert query == {
        "APPENDIX.__ARCHIVED__": "E",
        "APPENDIX.__RETRY_LEVEL__": {"$exists": False},
    }
    assert update["$set"]["APPENDIX.__ARCHIVED__"] == "R"
    assert update["$set"]["APPENDIX.__RETRY_LEVEL__"] == 1
    assert extension.migrated_legacy_errors == 4


def test_auto_replay_disabled_defers_unarchived_until_manual_trigger():
    runtime = HubRuntime()
    registry = Registry()
    extension = IISUnarchivedReplayExtension(registry, auto_replay=False)
    received = []
    runtime.install(extension)
    runtime.subscribe(ANALYSIS_REQUESTED, lambda event, _: received.append(event.payload))
    runtime.start()

    # 自动回放被禁用：启动后不应有未处理记录进入。
    assert not _wait_until(lambda: len(received) > 0, timeout=0.3)
    assert extension.replayed == 0

    # 手工触发后正常回放。
    result = extension.trigger_replay()
    assert result["started"] is True
    assert _wait_until(lambda: len(received) == 2)
    runtime.stop()
    assert extension.replayed == 2


def test_manual_trigger_rejects_concurrent_run_and_requires_start():
    extension = IISUnarchivedReplayExtension(Registry())
    assert extension.trigger_replay()["started"] is False  # 未 start

    runtime = HubRuntime()
    registry = Registry()
    extension = IISUnarchivedReplayExtension(registry, auto_replay=False)
    runtime.install(extension)
    runtime.start()
    try:
        extension._manual_running = True  # 模拟一次进行中的手工回放
        assert extension.trigger_replay()["reason"] == "already_running"
    finally:
        runtime.stop()


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class SortableCollection(Collection):
    """模拟 Mongo cursor：支持 sort(key, direction) 返回排序后的列表。"""

    def __init__(self):
        now = datetime.now(timezone.utc)
        self.records = [
            {"_id": "old", "UUID": "30eb0459-998d-486f-8eac-470b9dfd98ec",
             "informant": "https://example.test/old",
             "collect_time": now - timedelta(days=2)},
            {"_id": "new", "UUID": "a98f33b1-10a5-499a-85ac-eb3c773d2198",
             "informant": "https://example.test/new",
             "collect_time": now - timedelta(hours=1)},
        ]
        self.updates = []

    def update_many(self, query, update):
        self.updates.append((query, update))
        return SimpleNamespace(modified_count=len(query.get("_id", {}).get("$in", [])))

    def find(self, query):
        records = self.records

        class Cursor:
            def sort(self, key, direction):
                return sorted(records, key=lambda r: r.get(key),
                              reverse=(direction < 0))

            def __iter__(self):
                return iter(records)

        return Cursor()


def test_replay_unprocessed_emits_newest_first_when_enabled():
    runtime = HubRuntime()
    registry = Registry()
    registry.context.mongo_db_cache.collection = SortableCollection()
    extension = IISUnarchivedReplayExtension(registry, replay_newest_first=True)
    received = []
    runtime.subscribe(ANALYSIS_REQUESTED, lambda event, _: received.append(event.payload))

    extension._replay_unprocessed(runtime)
    runtime.start()
    assert runtime.wait_for_idle(timeout=2)
    runtime.stop()

    assert [item["informant"] for item in received] == [
        "https://example.test/new", "https://example.test/old"]


def test_replay_deprecates_records_beyond_analysis_age_limit():
    runtime = HubRuntime()
    registry = Registry()
    registry.context.mongo_db_cache.collection = SortableCollection()
    extension = IISUnarchivedReplayExtension(
        registry, replay_newest_first=False, max_analysis_age_hours=24)
    received = []
    runtime.subscribe(ANALYSIS_REQUESTED, lambda event, _: received.append(event.payload))

    extension._replay_unprocessed(runtime)
    runtime.start()
    assert runtime.wait_for_idle(timeout=2)
    runtime.stop()

    # 2 天前的旧记录被弃用不发射；1 小时前的正常回放
    assert [item["informant"] for item in received] == ["https://example.test/new"]
    deprecated_calls = [
        u for u in registry.context.mongo_db_cache.collection.updates
        if isinstance(u[1], dict) and "$set" in u[1]
        and u[1]["$set"].get("APPENDIX.__ARCHIVED__") == "X"
    ]
    assert deprecated_calls, "超旧记录应被批量标记为 X"
