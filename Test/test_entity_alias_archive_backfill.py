# -*- coding: utf-8 -*-
"""历史归档实体标准名回填测试。"""

from Scripts.entity_alias_archive_backfill import normalize_archive_document
from Test.test_event_v4_pipeline_adapter import _original, _valuable
from ServiceComponent.event_v4_archive import ArchivedDataV4, build_archive_appendix

from datetime import datetime, timezone


def _archive_document():
    now = datetime.now(timezone.utc)
    appendix = build_archive_appendix(
        _original(), domain_name="news", ai_service="fake", ai_model="fake",
        prompt_version=41, processed_at=now, archived_at=now, total_score=6.5)
    return ArchivedDataV4(
        **_valuable(), UUID=_original()["UUID"],
        INFORMANT=_original()["informant"], APPENDIX=appendix,
    ).model_dump(mode="python", by_alias=True)


def test_normalize_archive_document_rebuilds_projection_and_keeps_unknown_name():
    document = _archive_document()
    plan = normalize_archive_document(
        document, lambda name: {"A军": "甲军"}.get(name))

    assert plan.changes == ({
        "entity_id": "ENT1", "before": "A军", "after": "甲军",
    },)
    assert [entity.name for entity in plan.record.EVENT_EXTRACTION.entities] == [
        "甲军", "B设施"]
    assert plan.record.ORGANIZATION == ["甲军"]
    assert plan.resolve_errors == 0
    # 分类：A军=可替换别名；B设施=未匹配
    assert plan.replaceable_aliases == 1
    assert plan.unmatched == ("B设施",)
    assert plan.already_canonical == 0


def test_normalize_archive_document_classifies_already_canonical():
    document = _archive_document()
    # 所有名字都按原名返回 → 已是标准名，无变更
    plan = normalize_archive_document(document, lambda name: name)

    assert plan.changes == ()
    assert plan.already_canonical == 2
    assert plan.replaceable_aliases == 0
    assert plan.unmatched == ()

    # 混合：一个已是标准名，一个可替换，一个未匹配
    mapping = {"A军": "A军", "B设施": None}
    plan = normalize_archive_document(document, mapping.get)
    assert plan.already_canonical == 1
    assert plan.unmatched == ("B设施",)


def test_normalize_archive_document_keeps_original_on_resolver_error():
    def resolver(name):
        raise RuntimeError("offline")

    plan = normalize_archive_document(_archive_document(), resolver)

    assert plan.changes == ()
    assert [entity.name for entity in plan.record.EVENT_EXTRACTION.entities] == [
        "A军", "B设施"]
    assert plan.resolve_errors == 2
