from ServiceComponent.adapters import IISAsyncTranslationExtension
from ServiceComponent.extensions import TRANSLATION_COMPLETED
from ServiceComponent.pipeline import ARCHIVE_COMPLETED
from ServiceComponent.runtime import HubEvent, HubRuntime


class FakeTranslator:
    def __init__(self, shutdown, on_patched):
        self.shutdown = shutdown
        self.on_patched = on_patched
        self.started = False
        self.enqueued = []

    def start(self):
        self.started = True

    def enqueue_new(self, identifier, reason):
        self.enqueued.append((identifier, reason))


def test_translation_extension_defers_translated_records_and_emits_completion_event():
    holder = {}

    def factory(shutdown, callback):
        holder["translator"] = FakeTranslator(shutdown, callback)
        return holder["translator"]

    extension = IISAsyncTranslationExtension(
        default_subsystem="news", translator_factory=factory,
        needs_translation=lambda payload: payload.get("translate", False))
    runtime = HubRuntime()
    runtime.install(extension)
    completed = []
    runtime.subscribe(TRANSLATION_COMPLETED, lambda event, _: completed.append(event.payload))
    runtime.start()

    source = HubEvent(ARCHIVE_COMPLETED, {"UUID": "one", "translate": True}, subsystem="news")
    assert extension.should_defer_index(source)
    runtime.emit(source)
    runtime.emit(HubEvent(ARCHIVE_COMPLETED, {"UUID": "two"}, subsystem="news"))
    assert runtime.wait_for_idle(timeout=1)
    assert holder["translator"].started
    assert holder["translator"].enqueued == [("one", "new_archived")]

    holder["translator"].on_patched({"UUID": "one", "EVENT_TEXT": "中文"})
    assert runtime.wait_for_idle(timeout=1)
    assert completed == [{"UUID": "one", "EVENT_TEXT": "中文"}]
    runtime.stop()
