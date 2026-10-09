import time

from ServiceComponent.adapters import IISUnarchivedReplayExtension
from ServiceComponent.pipeline import ANALYSIS_REQUESTED
from ServiceComponent.runtime import HubRuntime


class Collection:
    def find(self, query):
        assert query == {"processing.status": {"$exists": False}}
        return [{"UUID": "old-1"}, {"UUID": "old-2"}]


class Cache:
    collection = Collection()


class Context:
    name = "news"
    mongo_db_cache = Cache()


class Registry:
    def enabled_subsystems(self):
        return [Context()]


def test_replay_extension_resumes_unarchived_cache_at_analysis_stage():
    runtime = HubRuntime()
    extension = IISUnarchivedReplayExtension(Registry())
    received = []
    runtime.install(extension)
    runtime.subscribe(ANALYSIS_REQUESTED, lambda event, _: received.append(event.payload["UUID"]))
    runtime.start()

    assert _wait_until(lambda: len(received) == 2)
    runtime.stop()
    assert received == ["old-1", "old-2"]
    assert extension.replayed == 2


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
