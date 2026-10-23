import time

from ServiceComponent.adapters import IISVectorExtension
from ServiceComponent.pipeline import ARCHIVE_COMPLETED
from ServiceComponent.runtime import HubEvent, HubRuntime


class FakeClient:
    def wait_until_ready(self, **kwargs):
        return None

    def create_collection(self, **kwargs):
        return kwargs["name"]


class FakeEngine:
    def __init__(self, collection):
        self.collection = collection
        self.indexed = []

    def upsert(self, record, *, data_type):
        self.indexed.append((record, data_type))

    def query(self, text, top_n, threshold, **filters):
        if self.collection == "intelligence_summary":
            return [{"doc_id": "a", "score": 0.7}, {"doc_id": "b", "score": 0.9}]
        return [{"doc_id": "a", "score": 0.8}, {"doc_id": "c", "score": 1.1}]


def build_extension():
    return IISVectorExtension(
        FakeClient(), default_subsystem="news", engine_factory=FakeEngine,
        record_factory=lambda payload: {"converted": payload["UUID"]}, retry_interval=0.01)


def test_vector_extension_indexes_only_default_subsystem_and_merges_search_results():
    extension = build_extension()
    runtime = HubRuntime()
    runtime.install(extension)
    runtime.start()
    assert _wait_until(lambda: extension.ready)

    runtime.emit(HubEvent(ARCHIVE_COMPLETED, {"UUID": "one"}, subsystem="news"))
    runtime.emit(HubEvent(ARCHIVE_COMPLETED, {"UUID": "two"}, subsystem="other"))
    assert runtime.wait_for_idle(timeout=1)
    assert extension.wait_for_index_idle(timeout=1)

    assert extension.summary_engine.indexed == [({"converted": "one"}, "summary")]
    assert extension.full_text_engine.indexed == [({"converted": "one"}, "full")]
    assert extension.search("test", in_fulltext=True, score_threshold_max=1.0) == [
        ("b", 0.9, {"doc_id": "b", "score": 0.9}),
        ("a", 0.8, {"doc_id": "a", "score": 0.8}),
    ]
    runtime.stop()


def test_vector_extension_without_client_remains_disabled():
    extension = IISVectorExtension(
        None, default_subsystem="news", engine_factory=FakeEngine,
        record_factory=dict)
    runtime = HubRuntime()
    runtime.install(extension)
    runtime.start()
    runtime.emit(HubEvent(ARCHIVE_COMPLETED, {"UUID": "one"}, subsystem="news"))
    assert runtime.wait_for_idle(timeout=1)
    assert not extension.ready
    assert extension.search("test") == []
    runtime.stop()


def _wait_until(condition, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()
