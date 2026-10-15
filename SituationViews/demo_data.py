"""无需 MongoDB 的页面演示数据，仅供独立原型预览。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ServiceComponent.intelligence_contracts import APPENDIX_CONTENT_TIME, APPENDIX_TOTAL_SCORE


class DemoCursor(list):
    def limit(self, count):
        return DemoCursor(self[:count])


class DemoCollection:
    def __init__(self, values):
        self.values = list(values)

    def find(self, query, projection=None):
        values = list(self.values)
        if "intelligence_uuid" in query:
            allowed = set(query["intelligence_uuid"].get("$in") or [])
            values = [item for item in values if item.get("intelligence_uuid") in allowed]
        if query.get("is_primary") is True:
            values = [item for item in values if item.get("is_primary") is True]
        return DemoCursor(dict(item) for item in values)


class DemoSubsystemRegistry:
    def __init__(self):
        now = datetime.now(timezone.utc)
        stories = [
            ("demo-1", 8.4, "黑海港口设施遭到袭击"),
            ("demo-2", 7.8, "能源设施再次遭到袭击"),
            ("demo-3", 7.2, "监管机构宣布新制裁"),
            ("demo-4", 6.8, "热带风暴造成区域性灾害"),
            ("demo-5", 8.1, "多方在边境地区发生武装冲突"),
        ]
        archives = [{
            "UUID": uuid,
            "EVENT_TITLE": title,
            "APPENDIX": {
                APPENDIX_CONTENT_TIME: now - timedelta(hours=index * 8),
                APPENDIX_TOTAL_SCORE: score,
            },
        } for index, (uuid, score, title) in enumerate(stories)]
        events = [
            _event("ev-1", "demo-1", "attack", "攻击", "俄罗斯军方", "乌克兰港口设施", "敖德萨", "UA"),
            _event("ev-2", "demo-2", "attack", "攻击", "俄罗斯军方", "乌克兰港口设施", "基辅", "UA"),
            _event("ev-3", "demo-3", "sanction", "制裁", "欧盟理事会", "俄罗斯企业", "布鲁塞尔", "BE", actor_role="authority"),
            _event("ev-4", "demo-4", "natural_hazard", "自然灾害", "热带风暴", "沿海地区", "菲律宾", "PH", actor_role="phenomenon", target_role="affected_area", topology="intrinsic"),
            {
                "event_uuid": "ev-5", "intelligence_uuid": "demo-5", "is_primary": True,
                "frame": {"topology": "relational"},
                "predicate": {"id": "armed_conflict", "label": "武装冲突"},
                "roles": {"belligerent": [
                    {"name": "甲方武装力量", "type": "organization", "country_code": "IL"},
                    {"name": "乙方武装力量", "type": "organization", "country_code": "LB"},
                ]},
                "event_location": [{"name": "边境地区", "type": "location", "country_code": "LB"}],
            },
        ]
        self.default_name = "demo"
        self.ctx = SimpleNamespace(
            name="demo", display_name="演示数据", is_default=True,
            event_v4_intelligence_collection=DemoCollection(archives),
            event_v4_event_collection=DemoCollection(events),
        )

    def resolve(self, name=None):
        return self.ctx if not name or name == "demo" else None

    def describe(self):
        return [{"name": "demo", "display_name": "演示数据", "is_default": True}]


def _event(event_uuid, article_uuid, predicate, label, actor, target, location, country,
           *, actor_role="actor", target_role="target", topology="targeted"):
    return {
        "event_uuid": event_uuid,
        "intelligence_uuid": article_uuid,
        "is_primary": True,
        "frame": {"topology": topology},
        "predicate": {"id": predicate, "label": label},
        "roles": {
            actor_role: [{"name": actor, "type": "organization"}],
            target_role: [{"name": target, "type": "facility", "country_code": country}],
        },
        "event_location": [{"name": location, "type": "location", "country_code": country}],
    }
