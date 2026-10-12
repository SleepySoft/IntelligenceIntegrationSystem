import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "static" / "js" / "article_detail_renderer.js"
CONTRACTS = ROOT / "static" / "js" / "intelligence_contracts.js"


def _render_event_section(article: dict) -> str:
    return _run_renderer("renderEventSection", article)


def _run_renderer(method: str, article: dict) -> str:
    if shutil.which("node") is None:
        pytest.skip("Node.js is required for the frontend renderer test")
    script = (
        "global.window = {};\n"
        + CONTRACTS.read_text(encoding="utf-8")
        + "\n"
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
        "events": [{
            "event_uuid": "event-1",
            "local_event_id": "E1",
            "is_primary": True,
            "predicate": {"id": "investigate", "surface": "启动调查", "gloss": None},
            "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
            "roles": {
                "investigator": [{"name": "甲机构", "type": "organization", "country_code": "CN"}],
                "object": [{"name": "乙公司", "type": "organization"}],
            },
            "role_semantics": {"investigator": "agent", "object": "affected"},
            "time": {
                "event_time": {
                    "normalized": "2026-10-11",
                    "precision": "day",
                    "approximate": False,
                    "surface": "周日",
                }
            },
            "event_location": [{"name": "上海", "type": "location"}],
            "attributes": {"amount": {"type": "money", "value": 20, "currency": "CNY", "unit": "亿元"}},
            "qualifiers": [{
                "id": "Q1", "type": "epistemic", "value": "asserted", "scope": "event",
                "time": {"normalized": "2026-10-11"},
                "by": [{"name": "甲机构", "type": "organization"}], "surface": "甲机构表示",
            }],
            "relations": [{"predicate": "causes", "target_event_uuid": "event-2", "surface": "由此前事件导致"}],
            "observed_at": "2026-10-11T08:30:00+08:00",
        }, {
            "event_uuid": "event-2",
            "local_event_id": "E2",
            "predicate": {"id": "restrict", "surface": "实施限制"},
            "frame": {"dynamics": "change", "topology": "targeted", "agency": "agentive"},
            "roles": {},
        }],
    }

    html = _render_event_section(article)

    assert "事件结构" in html
    assert "2 个事件" in html
    assert 'data-event-view="narrative"' in html
    assert 'data-event-view="audit"' in html
    assert 'data-event-view="relations"' in html
    assert 'data-event-select="event-1"' in html
    assert 'data-event-panel="event-2" hidden' in html
    assert "主事件" in html
    assert "启动调查" in html
    assert "事件解读" in html
    assert "全部信息" in html
    assert "事件联系" in html
    assert "谁在行动" in html
    assert "影响谁 / 什么" in html
    assert "2026-10-11" in html
    assert "dynamics" in html
    assert "过程" in html
    assert "investigate" in html
    assert "甲机构" in html
    assert "中国 · 机构" in html
    assert "乙公司" in html
    assert "上海" in html
    assert "20 亿元 CNY" in html
    assert "类型：money" in html
    assert "精度：day" in html
    assert "信息状态" in html
    assert "已陈述" in html
    assert "限定词 ID" in html
    assert "Q1" in html
    assert "适用范围" in html
    assert "表述主体" in html
    assert "启动调查</span><b>导致</b><span>实施限制" in html
    assert "查看结构化字段" in html
    assert "event-1" in html
    assert "以下内容是对来源报道的结构化整理" in html


def test_event_section_falls_back_to_extraction_and_escapes_content():
    article = {
        "EVENT_EXTRACTION": {
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

    html = _render_event_section(article)

    assert "存在" in html
    assert "主事件" in html
    assert "中国 · 机构" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<img src=x onerror=alert(1)>" not in html


def test_single_event_relations_view_explains_missing_explicit_edges():
    article = {
        "events": [{
            "event_uuid": "event-1",
            "local_event_id": "E1",
            "is_primary": True,
            "predicate": {"surface": "举行会谈"},
            "roles": {},
            "qualifiers": [],
            "relations": [],
        }]
    }

    html = _render_event_section(article)

    assert "1 个事件" in html
    assert "当前事件之间没有显式关系" in html
    assert 'data-event-panel="event-1"' in html
    assert 'data-event-panel="event-1" hidden' not in html


def test_event_story_makes_a_physical_targets_country_visible():
    article = {
        "events": [{
            "event_uuid": "event-attack-1",
            "local_event_id": "E1",
            "is_primary": True,
            "predicate": {"id": "attack", "surface": "袭击"},
            "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
            "roles": {
                "actor": [{"name": "俄罗斯", "type": "geopolitical_entity", "country_code": "RU"}],
                "target": [{"name": "发电厂", "type": "facility", "country_code": "UA"}],
            },
            "role_semantics": {"actor": "agent", "target": "affected"},
            "event_location": [{"name": "乌克兰", "type": "geopolitical_entity", "country_code": "UA"}],
            "qualifiers": [],
            "relations": [],
        }],
    }

    html = _render_event_section(article)

    assert "俄罗斯" in html
    assert "袭击" in html
    assert "发电厂" in html
    assert "乌克兰境内 · 设施" in html
    assert "事发地：乌克兰" in html


def test_event_section_is_hidden_without_events():
    assert _render_event_section({}) == ""


def test_article_detail_labels_collection_fallback_time():
    article = {
        "UUID": "intel-1",
        "content_time": "2026-10-11T04:00:00Z",
        "content_time_source": "collected",
        "EVENT_TITLE": "标题", "EVENT_BRIEF": "摘要", "EVENT_TEXT": "正文",
        "RATE": {},
    }

    html = _run_renderer("generateHTML", article)

    assert "采集时间：" in html
    assert "Publish:" not in html
