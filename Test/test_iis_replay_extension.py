import time
from uuid import UUID

from ServiceComponent.adapters import IISUnarchivedReplayExtension
from ServiceComponent.pipeline import ANALYSIS_REQUESTED
from ServiceComponent.runtime import HubRuntime


class Collection:
    def __init__(self):
        self.records = [
            {"_id": "mongo-id-1", "UUID": "old-1", "informant": "https://example.test/1"},
            {"_id": "mongo-id-2", "UUID": "old-2", "informant": "https://example.test/2"},
        ]
        self.updates = []

    def find(self, query):
        assert query == {
            "$and": [
                {"__ARCHIVED__": {"$exists": False}},
                {"APPENDIX.__ARCHIVED__": {"$exists": False}},
            ]
        }
        return self.records

    def update_one(self, query, update):
        self.updates.append((query, update))


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

    def enabled_subsystems(self):
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


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
