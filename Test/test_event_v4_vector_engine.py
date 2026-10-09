from datetime import datetime, timezone
from uuid import UUID

from ServiceComponent.IntelligenceHubDefines_v4 import validate_analysis_result_v4
from ServiceComponent.IntelligenceVectorDBEngine import IntelligenceVectorDBEngine
from ServiceComponent.event_v4_archive import ArchivedIntelligenceV4


class FakeVectorCollection:
    def __init__(self):
        self.documents = []

    def upsert(self, **kwargs):
        self.documents.append(kwargs)


def _archive():
    analysis = validate_analysis_result_v4({
        "kind": "valuable",
        "message": {"title": "测试标题", "brief": "测试摘要", "text": "测试正文"},
        "classification": {"taxonomy": "经济与金融", "subcategories": ["商业与市场"]},
        "assessment": {
            "impact": "市场影响", "reason": "发生明确事件", "tips": "",
            "rate": {"影响广度": 4, "影响深度": 5, "新颖性与异常性": 6,
                     "演化与连锁潜力": 5, "舆情及认知影响": 3, "可行动性": 6},
        },
        "event_extraction": {
            "schema_version": "event-extraction/1.0",
            "primary_event_id": "E1",
            "entities": [
                {"id": "ENT1", "name": "公司甲", "type": "organization"},
                {"id": "ENT2", "name": "设施乙", "type": "facility"},
            ],
            "events": [{"id": "E1", "core": {
                "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
                "predicate": {"id": "attack", "surface": "袭击"},
                "roles": {"actor": ["ENT1"], "target": ["ENT2"]},
            }}],
        },
    })
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    identifier = UUID("30eb0459-998d-486f-8eac-470b9dfd98ec")
    return ArchivedIntelligenceV4(
        intelligence_uuid=identifier,
        informant="https://example.test/v4",
        raw_data={"content": "原始全文", "pub_time": now},
        analysis=analysis,
        ai_service="fake://ai",
        ai_model="fake-v4",
        prompt_version=40,
        processed_at=now,
        archived_at=now,
        total_score=6.5,
        event_uuids=[identifier],
        primary_event_uuid=identifier,
        subsystem="news",
    )


def test_vector_engine_consumes_v4_archive_fields():
    collection = FakeVectorCollection()
    engine = IntelligenceVectorDBEngine(collection)

    engine.upsert(_archive(), "summary")

    stored = collection.documents[0]
    assert stored["doc_id"] == "30eb0459-998d-486f-8eac-470b9dfd98ec"
    assert stored["text"] == "测试标题\n\n测试摘要\n\n测试正文"
    assert stored["metadata"]["total_score"] == 6.5
    assert stored["metadata"]["schema_version"] == "iis-intelligence/4.0"


def test_vector_search_text_reads_nested_v4_message():
    document = _archive().model_dump(mode="json", by_alias=True)
    assert IntelligenceVectorDBEngine.build_search_text(document) == (
        "测试标题\n\n测试摘要\n\n测试正文")
