"""Event V4 的只读态势聚合，不参与分析、归档或事件索引写入。"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations, product
from typing import Any, Iterable

from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME,
    APPENDIX_FIELD,
    APPENDIX_TOTAL_SCORE,
    appendix_path,
)


CATEGORY_LABELS = {
    "war": "武装冲突",
    "security": "安全与管制",
    "politics": "政治与社会",
    "hazard": "灾害与卫生",
    "economy": "经济与金融",
    "industry": "产业与科技",
    "other": "其他",
}

# 独立视图的展示归类。Registry tag 优先；这里仅是可随实验删除的展示回退，
# 不参与 Event V4 的抽取、校验、身份合并或持久化契约。
PREDICATE_CATEGORIES = {
    "war": {"armed_conflict", "attack", "defend", "intercept"},
    "security": {
        "damage", "casualty", "sanction", "restrict", "violate", "detain",
        "seize", "observe", "disrupt", "control",
    },
    "politics": {
        "appoint", "remove", "elect", "adjudicate", "protest", "negotiate",
        "agree", "regulate", "investigate", "cooperate", "communicate",
    },
    "hazard": {"accident", "natural_hazard", "outbreak"},
    "economy": {
        "default", "insolvency", "trade", "acquire", "invest", "fund", "pay",
        "lend", "repay", "issue_bond", "rating_change", "declare_dividend",
    },
    "industry": {
        "shortage", "supply", "operate", "produce", "construct", "develop",
        "test", "deploy", "maintain", "build_facility", "expand_capacity",
        "suspend_production", "resume_production", "release", "create",
    },
}


@dataclass(frozen=True)
class ViewQuery:
    start: dt.datetime
    end: dt.datetime
    min_score: float = 6.0
    category: str | None = None
    primary_only: bool = True
    max_articles: int = 5000


class SituationViewEngine:
    """把归档文章与派生事件索引投影为地图和实体图响应。"""

    def __init__(self, intelligence_collection: Any, event_collection: Any, registry: Any):
        self.intelligence_collection = intelligence_collection
        self.event_collection = event_collection
        self.registry = registry

    def map_view(self, query: ViewQuery) -> dict[str, Any]:
        events, meta = self._load_events(query)
        countries: dict[str, dict[str, Any]] = {}
        mapped_events: set[str] = set()

        for event in events:
            category = self._category(event)
            if query.category and category != query.category:
                continue
            event_uuid = str(event.get("event_uuid") or "")
            locations = self._unique_entities(event.get("event_location") or [])
            country_groups: dict[str, list[dict]] = defaultdict(list)
            for location in locations:
                code = str(location.get("country_code") or "").upper()
                if re.fullmatch(r"[A-Z]{2}", code):
                    country_groups[code].append(location)

            for code, items in country_groups.items():
                mapped_events.add(event_uuid)
                bucket = countries.setdefault(code, {
                    "country_code": code,
                    "event_ids": set(),
                    "article_ids": set(),
                    "categories": Counter(),
                    "predicates": Counter(),
                    "locations": Counter(),
                    "score_sum": 0.0,
                    "score_max": 0.0,
                    "last_time": None,
                })
                if event_uuid in bucket["event_ids"]:
                    continue
                bucket["event_ids"].add(event_uuid)
                bucket["article_ids"].add(str(event.get("intelligence_uuid") or ""))
                bucket["categories"][category] += 1
                predicate = self._predicate_id(event)
                bucket["predicates"][predicate] += 1
                for item in items:
                    name = str(item.get("name") or "").strip()
                    if name:
                        bucket["locations"][name] += 1
                score = float(event.get("_view_score") or 0)
                bucket["score_sum"] += score
                bucket["score_max"] = max(bucket["score_max"], score)
                content_time = event.get("_view_content_time")
                if content_time and (bucket["last_time"] is None or content_time > bucket["last_time"]):
                    bucket["last_time"] = content_time

        hotspots = []
        for bucket in countries.values():
            count = len(bucket["event_ids"])
            categories = dict(bucket["categories"].most_common())
            dominant = next(iter(categories), "other")
            hotspots.append({
                "country_code": bucket["country_code"],
                "event_count": count,
                "article_count": len(bucket["article_ids"]),
                "average_score": round(bucket["score_sum"] / count, 2) if count else 0,
                "max_score": round(bucket["score_max"], 2),
                "dominant_category": dominant,
                "categories": categories,
                "predicates": dict(bucket["predicates"].most_common(8)),
                "locations": [
                    {"name": name, "count": value}
                    for name, value in bucket["locations"].most_common(12)
                ],
                "last_time": self._json_time(bucket["last_time"]),
            })
        hotspots.sort(key=lambda item: (-item["event_count"], item["country_code"]))

        eligible = sum(
            1 for event in events
            if not query.category or self._category(event) == query.category
        )
        meta.update({
            "eligible_event_count": eligible,
            "mapped_event_count": len(mapped_events),
            "location_coverage": round(len(mapped_events) / eligible, 4) if eligible else 0,
            "hotspot_count": len(hotspots),
        })
        return {"hotspots": hotspots, "meta": meta, "categories": CATEGORY_LABELS}

    def entity_graph(
        self,
        query: ViewQuery,
        *,
        min_support: int = 2,
        max_nodes: int = 80,
        max_edges: int = 240,
        keep_high_score_singletons: bool = True,
    ) -> dict[str, Any]:
        events, meta = self._load_events(query)
        node_data: dict[str, dict[str, Any]] = {}
        edge_data: dict[tuple[str, str, str, bool], dict[str, Any]] = {}
        considered = 0

        for event in events:
            category = self._category(event)
            if query.category and category != query.category:
                continue
            relations = self._entity_relations(event)
            if not relations:
                continue
            considered += 1
            event_uuid = str(event.get("event_uuid") or "")
            article_uuid = str(event.get("intelligence_uuid") or "")
            score = float(event.get("_view_score") or 0)
            predicate = self._predicate_id(event)
            for source, target, directed in relations:
                source_key = self._entity_key(source)
                target_key = self._entity_key(target)
                if not source_key or not target_key or source_key == target_key:
                    continue
                if not directed and target_key < source_key:
                    source, target = target, source
                    source_key, target_key = target_key, source_key
                self._touch_node(node_data, source_key, source, event_uuid, article_uuid, score)
                self._touch_node(node_data, target_key, target, event_uuid, article_uuid, score)
                edge_key = (source_key, target_key, predicate, directed)
                edge = edge_data.setdefault(edge_key, {
                    "source": source_key,
                    "target": target_key,
                    "predicate": predicate,
                    "predicate_label": self._predicate_label(event),
                    "category": category,
                    "directed": directed,
                    "event_ids": set(),
                    "article_ids": set(),
                    "score_sum": 0.0,
                    "score_max": 0.0,
                })
                if event_uuid not in edge["event_ids"]:
                    edge["event_ids"].add(event_uuid)
                    edge["article_ids"].add(article_uuid)
                    edge["score_sum"] += score
                    edge["score_max"] = max(edge["score_max"], score)

        high_score_cutoff = max(8.0, query.min_score + 2.0)
        retained = []
        for edge in edge_data.values():
            support = len(edge["event_ids"])
            if support < min_support and not (
                keep_high_score_singletons and edge["score_max"] >= high_score_cutoff
            ):
                continue
            edge["support"] = support
            edge["article_count"] = len(edge["article_ids"])
            edge["average_score"] = round(edge["score_sum"] / support, 2)
            retained.append(edge)
        retained.sort(key=lambda item: (-item["support"], -item["score_max"], item["predicate"]))

        importance = Counter()
        for edge in retained:
            importance[edge["source"]] += edge["support"]
            importance[edge["target"]] += edge["support"]
        selected_nodes = {key for key, _ in importance.most_common(max_nodes)}
        retained = [
            edge for edge in retained
            if edge["source"] in selected_nodes and edge["target"] in selected_nodes
        ][:max_edges]
        selected_nodes = {value for edge in retained for value in (edge["source"], edge["target"])}

        nodes = []
        for key in sorted(selected_nodes, key=lambda value: (-importance[value], value)):
            item = node_data[key]
            nodes.append({
                "id": key,
                "name": item["name"],
                "type": item["type"],
                "country_code": item.get("country_code"),
                "event_count": len(item["event_ids"]),
                "article_count": len(item["article_ids"]),
                "importance": importance[key],
                "max_score": round(item["score_max"], 2),
            })
        edges = [{key: value for key, value in edge.items()
                  if key not in {"event_ids", "article_ids", "score_sum"}}
                 for edge in retained]

        meta.update({
            "relational_event_count": considered,
            "raw_node_count": len(node_data),
            "raw_edge_count": len(edge_data),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "filtered_edge_count": max(0, len(edge_data) - len(edges)),
            "min_support": min_support,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
            "high_score_singleton_cutoff": high_score_cutoff if keep_high_score_singletons else None,
            "identity_mode": "normalized_name_type_country",
        })
        return {"nodes": nodes, "edges": edges, "meta": meta, "categories": CATEGORY_LABELS}

    def _load_events(self, query: ViewQuery) -> tuple[list[dict], dict[str, Any]]:
        archive_filter = {
            appendix_path(APPENDIX_CONTENT_TIME): {"$gte": query.start, "$lte": query.end},
            appendix_path(APPENDIX_TOTAL_SCORE): {"$gte": query.min_score},
        }
        projection = {
            "_id": 0,
            "UUID": 1,
            "EVENT_TITLE": 1,
            appendix_path(APPENDIX_CONTENT_TIME): 1,
            appendix_path(APPENDIX_TOTAL_SCORE): 1,
        }
        cursor = self.intelligence_collection.find(archive_filter, projection).limit(query.max_articles + 1)
        archives = list(cursor)
        truncated = len(archives) > query.max_articles
        if truncated:
            archives = archives[:query.max_articles]

        article_meta = {}
        for item in archives:
            appendix = item.get(APPENDIX_FIELD) or {}
            article_meta[str(item.get("UUID"))] = {
                "content_time": appendix.get(APPENDIX_CONTENT_TIME),
                "score": appendix.get(APPENDIX_TOTAL_SCORE),
                "title": item.get("EVENT_TITLE"),
            }
        identifiers = list(article_meta)
        event_filter: dict[str, Any] = {"intelligence_uuid": {"$in": identifiers}}
        if query.primary_only:
            event_filter["is_primary"] = True
        events = list(self.event_collection.find(event_filter, {"_id": 0})) if identifiers else []
        for event in events:
            data = article_meta.get(str(event.get("intelligence_uuid"))) or {}
            event["_view_content_time"] = data.get("content_time")
            event["_view_score"] = data.get("score")
            event["_view_title"] = data.get("title")
        return events, {
            "start": self._json_time(query.start),
            "end": self._json_time(query.end),
            "min_score": query.min_score,
            "primary_only": query.primary_only,
            "article_count": len(archives),
            "event_count": len(events),
            "article_limit": query.max_articles,
            "truncated": truncated,
        }

    def _category(self, event: dict) -> str:
        predicate = self._predicate_id(event)
        spec = self.registry.get(predicate) if self.registry else None
        tags = set(getattr(spec, "tags", ()) or ())
        if "war" in tags:
            return "war"
        for category, predicates in PREDICATE_CATEGORIES.items():
            if predicate in predicates:
                return category
        return "other"

    @staticmethod
    def _predicate_id(event: dict) -> str:
        return str((event.get("predicate") or {}).get("id") or "open")

    @staticmethod
    def _predicate_label(event: dict) -> str:
        predicate = event.get("predicate") or {}
        return str(predicate.get("label") or predicate.get("gloss") or predicate.get("surface") or predicate.get("id") or "未归类")

    def _entity_relations(self, event: dict) -> list[tuple[dict, dict, bool]]:
        roles = event.get("roles") or {}
        predicate = self._predicate_id(event)
        spec = self.registry.get(predicate) if self.registry else None
        arguments = getattr(spec, "arguments", None)
        subjects = self._entities_for_roles(roles, getattr(arguments, "subject_roles", ()))
        objects = self._entities_for_roles(roles, getattr(arguments, "object_roles", ()))
        if subjects and objects:
            return [(left, right, True) for left, right in product(subjects, objects) if left != right]

        topology = str(((event.get("frame") or {}).get("topology") or ""))
        if topology == "relational" and len(subjects) >= 2:
            # 多方关系仅生成相邻组合的无向投影，最多 12 个参与方，避免完全图失控。
            return [(left, right, False) for left, right in combinations(subjects[:12], 2)]
        return []

    @staticmethod
    def _entities_for_roles(roles: dict, role_names: Iterable[str]) -> list[dict]:
        values = []
        seen = set()
        for role in role_names or ():
            for entity in roles.get(role, ()) or ():
                key = SituationViewEngine._entity_key(entity)
                if key and key not in seen:
                    seen.add(key)
                    values.append(entity)
        return values

    @staticmethod
    def _unique_entities(values: Iterable[dict]) -> list[dict]:
        result, seen = [], set()
        for value in values:
            key = SituationViewEngine._entity_key(value)
            if key and key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @staticmethod
    def _entity_key(entity: dict) -> str:
        if not isinstance(entity, dict):
            return ""
        name = re.sub(r"\s+", " ", str(entity.get("name") or "").strip()).casefold()
        entity_type = str(entity.get("type") or "other").strip().casefold()
        country = str(entity.get("country_code") or "").strip().upper()
        return f"{entity_type}|{country}|{name}" if name else ""

    @staticmethod
    def _touch_node(store, key, entity, event_uuid, article_uuid, score):
        item = store.setdefault(key, {
            "name": str(entity.get("name") or "").strip(),
            "type": str(entity.get("type") or "other"),
            "country_code": entity.get("country_code"),
            "event_ids": set(),
            "article_ids": set(),
            "score_max": 0.0,
        })
        item["event_ids"].add(event_uuid)
        item["article_ids"].add(article_uuid)
        item["score_max"] = max(item["score_max"], score)

    @staticmethod
    def _json_time(value):
        if isinstance(value, dt.datetime):
            return value.isoformat()
        return value
