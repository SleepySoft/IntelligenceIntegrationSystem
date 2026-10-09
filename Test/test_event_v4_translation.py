import threading

from ServiceComponent.AsyncTranslationPatch import (
    AsyncTranslationPatch,
    TranslationTask,
    needs_translation,
)


class FakeQueryEngine:
    def __init__(self, document):
        self.document = document

    def get_intelligence(self, identifier, light_weight=False):
        return self.document if identifier == self.document["_id"] else None


class FakeCollection:
    def __init__(self):
        self.updates = []

    def update_one(self, query, update):
        self.updates.append((query, update))


def test_translation_patch_reads_and_updates_v4_message_fields():
    document = {
        "_id": "item-1",
        "analysis": {"message": {
            "title": "Market shock",
            "brief": "A material market shock happened today.",
            "text": "The event changed prices across several markets.",
        }},
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
    assert query == {"_id": "item-1"}
    assert update["$set"]["analysis.message.title"] == "市场冲击"
    assert "translation_revision" in update["$set"]
    assert patched[0]["analysis"]["message"]["brief"] == "市场发生显著冲击。"
