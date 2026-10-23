from datetime import datetime, timezone
from uuid import UUID

from ServiceComponent.IntelligenceHubDefines_v4 import validate_analysis_result_v4
from ServiceComponent.IntelligenceVectorDBEngine import IntelligenceVectorDBEngine
from ServiceComponent.event_v4_archive import ArchivedDataV4, build_archive_appendix


class FakeVectorCollection:
    def __init__(self): self.documents = []
    def upsert(self, **kwargs): self.documents.append(kwargs)


def _archive():
    analysis = validate_analysis_result_v4({
        "TAXONOMY": "经济与金融", "SUB_CATEGORY": ["商业与市场"],
        "EVENT_TITLE": "测试标题", "EVENT_BRIEF": "测试摘要", "EVENT_TEXT": "测试正文",
        "IMPACT": "市场影响", "REASON": "发生明确事件", "TIPS": "",
        "RATE": {"影响广度": 4, "影响深度": 5, "新颖性与异常性": 6,
                 "演化与连锁潜力": 5, "舆情及认知影响": 3, "可行动性": 6},
        "EVENT_EXTRACTION": {
            "schema_version": "event-extraction/1.0", "primary_event_id": "E1",
            "entities": [{"id": "ENT1", "name": "公司甲", "type": "organization"},
                         {"id": "ENT2", "name": "设施乙", "type": "facility"}],
            "events": [{"id": "E1", "core": {
                "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
                "predicate": {"id": "attack", "surface": "袭击"},
                "roles": {"actor": ["ENT1"], "target": ["ENT2"]}}}]},
    })
    now = datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc)
    identifier = UUID("30eb0459-998d-486f-8eac-470b9dfd98ec")
    appendix = build_archive_appendix(
        {"content": "原始全文", "pub_time": now}, subsystem="news",
        ai_service="fake://ai", ai_model="fake-v4", prompt_version=40,
        processed_at=now, archived_at=now, total_score=6.5)
    return ArchivedDataV4(**analysis.model_dump(mode="python"), UUID=str(identifier),
                          INFORMANT="https://example.test/v4", APPENDIX=appendix)


def test_vector_engine_consumes_v4_archive_fields():
    collection = FakeVectorCollection()
    engine = IntelligenceVectorDBEngine(collection)
    archive = _archive()
    engine.upsert(archive, "summary")
    stored = collection.documents[0]
    assert stored["doc_id"] == archive.UUID
    assert stored["text"] == "测试标题\n\n测试摘要\n\n测试正文"
    assert stored["metadata"]["total_score"] == 6.5
    assert stored["metadata"]["schema_version"] == "iis-intelligence/4.0"
    assert stored["metadata"]["content_time_source"] == "published"


def test_vector_search_text_reads_v2_style_top_level_message():
    document = _archive().model_dump(mode="json")
    assert IntelligenceVectorDBEngine.build_search_text(document) == "测试标题\n\n测试摘要\n\n测试正文"
