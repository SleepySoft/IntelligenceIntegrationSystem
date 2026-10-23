import threading

from ServiceComponent.AsyncTranslationPatch import (
    AsyncTranslationPatch,
    TranslationTask,
    needs_translation,
)
from ServiceComponent.intelligence_contracts import APPENDIX_TRANSLATED_REVISION, appendix_path


class FakeQueryEngine:
    def __init__(self, document):
        self.document = document

    def get_intelligence(self, identifier, light_weight=False, include_appendix=False):
        return self.document if identifier == self.document["UUID"] else None


class FakeCollection:
    def __init__(self):
        self.updates = []

    def update_one(self, query, update):
        self.updates.append((query, update))


def test_translation_patch_reads_and_updates_v4_message_fields():
    document = {
        "UUID": "item-1",
        "EVENT_TITLE": "Market shock",
        "EVENT_BRIEF": "A material market shock happened today.",
        "EVENT_TEXT": "The event changed prices across several markets.",
        "APPENDIX": {},
    }
    collection = FakeCollection()
    patched = []
    service = AsyncTranslationPatch(
        mongo_db_archive=collection,
        query_engine=FakeQueryEngine(document),
        ai_client_manager=object(),
        shutdown_flag=threading.Event(),
        on_patched=patched.append,
        backfill_enabled=False,
    )
    service._translate_via_ai = lambda _: ("市场冲击", "市场发生显著冲击。", "事件改变了多个市场价格。")

    assert needs_translation(document)
    service._process_task(TranslationTask("item-1", 0, "test"))

    query, update = collection.updates[0]
    assert query == {"UUID": "item-1"}
    assert update["$set"]["EVENT_TITLE"] == "市场冲击"
    assert appendix_path(APPENDIX_TRANSLATED_REVISION) in update["$set"]
    assert patched[0]["EVENT_BRIEF"] == "市场发生显著冲击。"
