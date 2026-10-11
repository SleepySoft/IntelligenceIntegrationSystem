import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "static" / "js" / "article_detail_renderer.js"


def _render_event_section(article: dict) -> str:
    return _run_renderer("renderEventSection", article)


def _run_renderer(method: str, article: dict) -> str:
    if shutil.which("node") is None:
        pytest.skip("Node.js is required for the frontend renderer test")
    script = (
        "global.window = {};\n"
        + RENDERER.read_text(encoding="utf-8")
        + f"\nprocess.stdout.write(window.ArticleDetailRenderer.{method}("
        + json.dumps(article, ensure_ascii=False)
        + "));\n"
    )
    result = subprocess.run(
        ["node"],
        input=script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_event_section_renders_normalized_v4_event_readably():
    article = {
        "primary_event_uuid": "event-1",
        "entities": [
            {"_id": "entity-a", "canonical_name": "甲机构", "entity_type": "organization"},
            {"_id": "entity-b", "canonical_name": "乙公司", "entity_type": "organization"},
            {"_id": "entity-place", "canonical_name": "上海", "entity_type": "location"},
        ],
        "events": [{
            "_id": "event-1",
            "local_event_id": "E1",
            "is_primary": True,
            "predicate": {"id": "investigate", "surface": "启动调查", "gloss": None},
            "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
            "role_bindings": [
                {"role": "investigator", "entity_uuid": "entity-a", "semantic_group": "agent"},
                {"role": "object", "entity_uuid": "entity-b", "semantic_group": "affected"},
            ],
            "time": {
                "event_time": {
                    "normalized": "2026-10-11",
                    "precision": "day",
                    "approximate": False,
                    "surface": "周日",
                }
            },
            "location_entity_uuids": ["entity-place"],
            "attributes": {"amount": {"type": "money", "value": 20, "currency": "CNY", "unit": "亿元"}},
            "qualifiers": [{
                "type": "epistemic", "value": "asserted", "scope": "event",
                "by": ["entity-a"], "surface": "甲机构表示",
            }],
            "relations": [{"predicate": "causes", "target_event_uuid": "event-2", "surface": "由此前事件导致"}],
            "observed_at": "2026-10-11T08:30:00+08:00",
        }, {
            "_id": "event-2",
            "local_event_id": "E2",
            "predicate": {"id": "restrict", "surface": "实施限制"},
            "frame": {"dynamics": "change", "topology": "targeted", "agency": "agentive"},
            "role_bindings": [],
        }],
    }

    html = _render_event_section(article)

    assert "事件结构" in html
    assert "2 个事件" in html
    assert "主事件" in html
    assert "启动调查" in html
    assert "甲机构" in html
    assert "乙公司" in html
    assert "上海" in html
    assert "CNY 20 亿元" in html
    assert "信息状态" in html
    assert "已陈述" in html
    assert "导致 <b>E2</b>" in html
    assert "以下内容是对来源报道的结构化整理" in html


def test_event_section_falls_back_to_extraction_and_escapes_content():
    article = {
        "analysis": {
            "event_extraction": {
                "primary_event_id": "E1",
                "entities": [{
                    "id": "ENT1", "name": "<img src=x onerror=alert(1)>",
                    "type": "organization", "country_code": "CN",
                }],
                "events": [{
                    "id": "E1",
                    "core": {
                        "frame": {"dynamics": "state", "topology": "intrinsic", "agency": "unknown"},
                        "predicate": {"id": "exist", "surface": "存在"},
                        "roles": {"subject": ["ENT1"]},
                    },
                    "qualifiers": [],
                    "relations": [],
                }],
            }
        }
    }

    html = _render_event_section(article)

    assert "存在" in html
    assert "主事件" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<img src=x onerror=alert(1)>" not in html


def test_event_section_is_hidden_without_events():
    assert _render_event_section({"analysis": {}}) == ""


def test_article_detail_labels_collection_fallback_time():
    article = {
        "_id": "intel-1",
        "content_time": "2026-10-11T04:00:00Z",
        "content_time_source": "collected",
        "raw_data": {
            "collect_time": "2026-10-11T04:00:00Z",
            "pub_time": None,
        },
        "analysis": {
            "message": {"title": "标题", "brief": "摘要", "text": "正文"},
            "assessment": {},
        },
    }

    html = _run_renderer("generateHTML", article)

    assert "采集时间：" in html
    assert "Publish:" not in html
