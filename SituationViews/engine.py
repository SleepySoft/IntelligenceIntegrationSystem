"""Event V4 的只读态势聚合，不参与分析、归档或事件索引写入。"""

from __future__ import annotations

import datetime as dt
import calendar
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations, product
from typing import Any, Iterable
from urllib.parse import urlparse

from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME,
    APPENDIX_CONTENT_TIME_SOURCE,
    APPENDIX_FIELD,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TIME_GOT,
    APPENDIX_TIME_PUB,
    APPENDIX_TOTAL_SCORE,
    appendix_path,
)


logger = logging.getLogger(__name__)


CATEGORY_LABELS = {
    "war": "武装冲突",
    "terror": "恐怖主义",
    "security": "安全与管制",
    "politics": "政治与社会",
    "hazard": "灾害与卫生",
    "economy": "经济与金融",
    "industry": "产业与科技",
    "other": "其他",
}

ENTITY_TYPE_GROUPS = frozenset({
    "person", "organization", "geopolitical_entity", "location", "facility", "other",
})

# 态势视图只读取派生事件索引中实际参与投影的字段。三个具名投影与页面用途绑定，
# 不接受浏览器传入 fields，避免为了一个视图重新读取完整事件文档。
MAP_EVENT_PROJECTION = {
    "event_uuid": 1,
    "intelligence_uuid": 1,
    "predicate": 1,
    "event_location": 1,
    "is_primary": 1,
}
GRAPH_EVENT_PROJECTION = {
    "event_uuid": 1,
    "intelligence_uuid": 1,
    "frame.topology": 1,
    "predicate": 1,
    "roles": 1,
    "qualifiers.type": 1,
    "qualifiers.value": 1,
    "is_primary": 1,
}
TIMELINE_EVENT_PROJECTION = {
    **GRAPH_EVENT_PROJECTION,
    "time": 1,
}

# 独立视图的展示归类。Registry tag 优先；这里仅是展示层的兼容回退，
# 不参与 Event V4 的抽取、校验、身份合并或持久化契约。
PREDICATE_CATEGORIES = {
    "war": {"armed_conflict", "attack", "defend", "intercept"},
    "security": {
        "damage", "casualty", "sanction", "restrict", "violate", "detain",
        "seize", "observe", "disrupt", "control",
        "kill", "assault", "kidnap", "rob",  # kill/rob 已并入 assault（registry 1.5），保留映射兼容旧数据
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
    categories: frozenset[str] | None = None
    primary_only: bool = True
    max_articles: int = 5000


class SituationViewEngine:
    """把归档文章与派生事件索引投影为地图和实体图响应。"""

    def __init__(self, intelligence_collection: Any, event_collection: Any, registry: Any,
                 *, subsystem: str = "", entity_resolver=None, entity_registry=None):
        self.intelligence_collection = intelligence_collection
        self.event_collection = event_collection
        self.registry = registry
        self.subsystem = str(subsystem or "").strip()
        self.entity_resolver = entity_resolver
        self.entity_registry = entity_registry

    def map_view(self, query: ViewQuery) -> dict[str, Any]:
        events, meta = self._load_events(query, MAP_EVENT_PROJECTION)
        countries: dict[str, dict[str, Any]] = {}
        mapped_events: set[str] = set()

        for event in events:
            category = self._category(event)
            if query.categories is not None and category not in query.categories:
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
                    # 供浏览器在已加载窗口内做时间投影。数组格式比重复字段名更紧凑：
                    # [时间戳秒, 事件 UUID, 文章 UUID, 评分, 类别, 谓词标签, 地点名列表]。
                    "timeline": [],
                })
                if event_uuid in bucket["event_ids"]:
                    continue
                bucket["event_ids"].add(event_uuid)
                bucket["article_ids"].add(str(event.get("intelligence_uuid") or ""))
                bucket["categories"][category] += 1
                predicate = self._predicate_id(event)
                bucket["predicates"][predicate] += 1
                location_names = []
                for item in items:
                    name = str(item.get("name") or "").strip()
                    if name:
                        bucket["locations"][name] += 1
                        location_names.append(name)
                score = float(event.get("_view_score") or 0)
                bucket["score_sum"] += score
                bucket["score_max"] = max(bucket["score_max"], score)
                content_time = event.get("_view_content_time")
                if content_time and (bucket["last_time"] is None or content_time > bucket["last_time"]):
                    bucket["last_time"] = content_time
                bucket["timeline"].append([
                    self._time_sort_value(content_time),
                    event_uuid,
                    str(event.get("intelligence_uuid") or ""),
                    score,
                    category,
                    predicate,
                    location_names,
                ])

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
                "timeline": sorted(bucket["timeline"], key=lambda item: item[0]),
            })
        hotspots.sort(key=lambda item: (-item["event_count"], item["country_code"]))

        eligible = sum(
            1 for event in events
            if query.categories is None or self._category(event) in query.categories
        )
        meta.update({
            "eligible_event_count": eligible,
            "mapped_event_count": len(mapped_events),
            "location_coverage": round(len(mapped_events) / eligible, 4) if eligible else 0,
            "hotspot_count": len(hotspots),
        })
        meta = {key: meta[key] for key in (
            "start", "end", "event_count", "truncated", "mapped_event_count",
            "location_coverage", "hotspot_count",
        )}
        return {"hotspots": hotspots, "meta": meta}

    def map_intelligences(
        self,
        query: ViewQuery,
        country_code: str,
        *,
        location_name: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """返回一个地图热点对应的真实情报摘要，供点击热点后按需加载。"""
        code = str(country_code or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", code):
            raise ValueError("country_code 必须是 ISO 两位国家代码")
        normalized_location = " ".join(str(location_name or "").casefold().split())

        events, meta = self._load_events(query, MAP_EVENT_PROJECTION)
        articles: dict[str, dict[str, Any]] = {}
        for event in events:
            category = self._category(event)
            if query.categories is not None and category not in query.categories:
                continue
            locations = self._unique_entities(event.get("event_location") or [])
            matched_locations = [
                item for item in locations
                if str(item.get("country_code") or "").strip().upper() == code
            ]
            if normalized_location:
                matched_locations = [
                    item for item in matched_locations
                    if " ".join(str(item.get("name") or "").casefold().split())
                    == normalized_location
                ]
            if not matched_locations:
                continue

            article_uuid = str(event.get("intelligence_uuid") or "").strip()
            if not article_uuid:
                continue
            item = articles.setdefault(article_uuid, {
                "uuid": article_uuid,
                "title": str(event.get("_view_title") or "未命名情报"),
                "score": float(event.get("_view_score") or 0),
                "content_time": event.get("_view_content_time"),
                "event_ids": set(),
                "predicates": Counter(),
            })
            event_uuid = str(event.get("event_uuid") or "")
            if event_uuid in item["event_ids"]:
                continue
            item["event_ids"].add(event_uuid)
            item["predicates"][self._predicate_label(event)] += 1

        ordered = sorted(
            articles.values(),
            key=lambda item: (
                -self._time_sort_value(item["content_time"]),
                -item["score"],
                item["uuid"],
            ),
        )
        page = ordered[offset:offset + limit]
        results = []
        for item in page:
            results.append({
                "uuid": item["uuid"],
                "title": item["title"],
                "score": round(item["score"], 2),
                "content_time": self._json_time(item["content_time"]),
                "event_count": len(item["event_ids"]),
                "predicates": [
                    {"label": name, "count": count}
                    for name, count in item["predicates"].most_common(6)
                ],
            })
        meta = {
            "country_code": code,
            "location_name": str(location_name or "").strip() or None,
            "total": len(ordered),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(results) < len(ordered),
        }
        return {"intelligences": results, "meta": meta}

    def map_locations(
        self,
        query: ViewQuery,
        country_codes: set[str],
        *,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        """聚合当前视野国家的明确地点名称，坐标由独立地名解析器补充。"""
        codes = {
            str(value or "").strip().upper() for value in country_codes
            if re.fullmatch(r"[A-Za-z]{2}", str(value or "").strip())
        }
        if not codes:
            return []
        events, _ = self._load_events(query, MAP_EVENT_PROJECTION)
        locations: dict[tuple[str, str], dict[str, Any]] = {}
        for event in events:
            category = self._category(event)
            if query.categories is not None and category not in query.categories:
                continue
            event_uuid = str(event.get("event_uuid") or "")
            article_uuid = str(event.get("intelligence_uuid") or "")
            for location in self._unique_entities(event.get("event_location") or []):
                code = str(location.get("country_code") or "").strip().upper()
                name = str(location.get("name") or "").strip()
                if code not in codes or not name:
                    continue
                key = (code, " ".join(name.casefold().split()))
                item = locations.setdefault(key, {
                    "name": name,
                    "country_code": code,
                    "event_ids": set(),
                    "article_ids": set(),
                    "categories": Counter(),
                    "score_max": 0.0,
                })
                if event_uuid in item["event_ids"]:
                    continue
                item["event_ids"].add(event_uuid)
                item["article_ids"].add(article_uuid)
                item["categories"][category] += 1
                item["score_max"] = max(
                    item["score_max"], float(event.get("_view_score") or 0))

        ordered = sorted(
            locations.values(),
            key=lambda item: (-len(item["event_ids"]), -item["score_max"], item["name"]),
        )[:limit]
        result = []
        for item in ordered:
            categories = dict(item["categories"].most_common())
            result.append({
                "name": item["name"],
                "country_code": item["country_code"],
                "event_count": len(item["event_ids"]),
                "article_count": len(item["article_ids"]),
                "max_score": round(item["score_max"], 2),
                "dominant_category": next(iter(categories), "other"),
            })
        return result

    def entity_graph(
        self,
        query: ViewQuery,
        *,
        min_support: int = 2,
        max_nodes: int = 80,
        max_edges: int = 240,
        keep_high_score_singletons: bool = True,
        entity_types: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        events, meta = self._load_events(query, GRAPH_EVENT_PROJECTION)
        node_data: dict[str, dict[str, Any]] = {}
        edge_data: dict[tuple[str, str, str, bool], dict[str, Any]] = {}
        prepared_events, country_hints = self._prepare_graph_events(events, query)
        temporal_events: list[list[Any]] = []
        temporal_refs: dict[str, int] = {}

        def temporal_ref(event: dict) -> int:
            event_uuid = str(event.get("event_uuid") or "")
            existing = temporal_refs.get(event_uuid)
            if existing is not None:
                return existing
            reference = len(temporal_events)
            temporal_refs[event_uuid] = reference
            temporal_events.append([
                self._time_sort_value(event.get("_view_content_time")),
                str(event.get("intelligence_uuid") or ""),
                float(event.get("_view_score") or 0),
                1 if self._is_negated(event) else 0,
            ])
            return reference

        inferred_country_nodes = set()
        for event, category, relations in prepared_events:
            event_uuid = str(event.get("event_uuid") or "")
            article_uuid = str(event.get("intelligence_uuid") or "")
            score = float(event.get("_view_score") or 0)
            predicate = self._predicate_id(event)
            for source, target, directed in relations:
                if entity_types is not None and (
                    self._entity_type_group(source) not in entity_types
                    or self._entity_type_group(target) not in entity_types
                ):
                    continue
                source_key, source_inferred = self._graph_entity_key(source, country_hints)
                target_key, target_inferred = self._graph_entity_key(target, country_hints)
                if not source_key or not target_key or source_key == target_key:
                    continue
                if source_inferred:
                    inferred_country_nodes.add(source_key)
                if target_inferred:
                    inferred_country_nodes.add(target_key)
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
                    "predicate_label": self._canonical_predicate_label(event),
                    "category": category,
                    "directed": directed,
                    "event_ids": set(),
                    "article_ids": set(),
                    "negated_event_ids": set(),
                    "score_sum": 0.0,
                    "score_max": 0.0,
                    "surface_labels": Counter(),
                    "event_refs": set(),
                })
                if event_uuid not in edge["event_ids"]:
                    edge["event_ids"].add(event_uuid)
                    edge["article_ids"].add(article_uuid)
                    edge["score_sum"] += score
                    edge["score_max"] = max(edge["score_max"], score)
                    edge["event_refs"].add(temporal_ref(event))
                    # 否定关系（polarity=negated）必须计入边：否则“未发生/已取消”
                    # 在图上与正向关系同形，意图一眼相反。
                    if self._is_negated(event):
                        edge["negated_event_ids"].add(event_uuid)
                    surface_label = self._predicate_label(event)
                    if surface_label:
                        edge["surface_labels"][surface_label] += 1

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

        # 剔除被节点/边上限淘汰后已经没有任何引用的时间记录，并重排为连续下标。
        used_temporal_refs = {
            reference
            for edge in retained
            for reference in edge["event_refs"]
        }
        for key in selected_nodes:
            used_temporal_refs.update(
                temporal_refs[event_uuid]
                for event_uuid in node_data[key]["event_ids"]
                if event_uuid in temporal_refs
            )
        ordered_temporal_refs = sorted(used_temporal_refs)
        temporal_remap = {
            old_reference: new_reference
            for new_reference, old_reference in enumerate(ordered_temporal_refs)
        }
        temporal_events = [temporal_events[reference] for reference in ordered_temporal_refs]
        temporal_refs = {
            event_uuid: temporal_remap[reference]
            for event_uuid, reference in temporal_refs.items()
            if reference in temporal_remap
        }
        for edge in retained:
            edge["event_refs"] = {
                temporal_remap[reference] for reference in edge["event_refs"]
            }

        nodes = []
        for key in sorted(selected_nodes, key=lambda value: (-importance[value], value)):
            item = node_data[key]
            nodes.append({
                "id": key,
                "name": item["name"],
                "type": item["type"],
                "country_code": item.get("country_code"),
                "identity_kind": item.get("identity_kind", "legacy"),
                "target_id": item.get("target_id"),
                "reference_count": len(item.get("reference_keys") or ()),
                "editable": bool(item.get("reference_keys")),
                "wiki_name": item.get("wiki_name"),
                "surfaces": [
                    {"name": name, "count": count}
                    for name, count in item.get("surfaces", Counter()).most_common(6)
                ],
                "event_count": len(item["event_ids"]),
                "article_count": len(item["article_ids"]),
                "importance": importance[key],
                "max_score": round(item["score_max"], 2),
                "event_refs": sorted(
                    temporal_refs[event_uuid]
                    for event_uuid in item["event_ids"]
                    if event_uuid in temporal_refs
                ),
            })
        edges = []
        for edge in retained:
            payload = {
                key: value for key, value in edge.items()
                if key not in {
                    "event_ids", "article_ids", "score_sum", "surface_labels",
                    "negated_event_ids",
                    "event_refs",
                }
            }
            payload["event_refs"] = sorted(edge["event_refs"])
            payload["negated_count"] = len(edge["negated_event_ids"])
            payload["surface_labels"] = [
                {"label": label, "count": count}
                for label, count in edge["surface_labels"].most_common(8)
                if label != edge["predicate_label"]
            ]
            edges.append(payload)

        meta.update({
            "relational_event_count": len(prepared_events),
            "raw_node_count": len(node_data),
            "raw_edge_count": len(edge_data),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "filtered_edge_count": max(0, len(edge_data) - len(edges)),
            "min_support": min_support,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
            "high_score_singleton_cutoff": high_score_cutoff if keep_high_score_singletons else None,
            "identity_mode": (
                "entity_ref_resolution_with_surface_fallback"
                if self.entity_resolver or self.entity_registry
                else "normalized_name_type_country_with_unique_country_fill"
            ),
            "inferred_country_node_count": len(inferred_country_nodes),
            "entity_types": sorted(entity_types) if entity_types is not None else None,
        })
        meta = {key: meta[key] for key in (
            "start", "end", "truncated", "relational_event_count", "node_count",
            "edge_count", "filtered_edge_count",
        )}
        return {
            "nodes": nodes,
            "edges": edges,
            "temporal_events": temporal_events,
            "meta": meta,
        }

    def entity_graph_intelligences(
        self,
        query: ViewQuery,
        *,
        selection_type: str,
        entity_id: str | None = None,
        source_id: str | None = None,
        target_id: str | None = None,
        predicate: str | None = None,
        directed: bool = True,
        role: str = "all",
        entity_types: frozenset[str] | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """返回支撑所选图节点或边的真实情报，不受图规模裁剪参数影响。"""
        if selection_type not in {"node", "edge"}:
            raise ValueError("selection_type 必须是 node 或 edge")
        if selection_type == "node" and not entity_id:
            raise ValueError("节点查询需要 entity_id")
        if selection_type == "edge" and not all((source_id, target_id, predicate)):
            raise ValueError("边查询需要 source_id、target_id 和 predicate")
        if role not in {"all", "subject", "object", "participant"}:
            raise ValueError("role 必须是 all、subject、object 或 participant")

        events, meta = self._load_events(query, GRAPH_EVENT_PROJECTION)
        prepared_events, country_hints = self._prepare_graph_events(events, query)
        articles: dict[str, dict[str, Any]] = {}

        for event, category, relations in prepared_events:
            article_uuid = str(event.get("intelligence_uuid") or "").strip()
            if not article_uuid:
                continue
            event_uuid = str(event.get("event_uuid") or "")
            predicate_id = self._predicate_id(event)
            predicate_label = self._canonical_predicate_label(event)
            for source, target, relation_directed in relations:
                if entity_types is not None and (
                    self._entity_type_group(source) not in entity_types
                    or self._entity_type_group(target) not in entity_types
                ):
                    continue
                source_key, _ = self._graph_entity_key(source, country_hints)
                target_key, _ = self._graph_entity_key(target, country_hints)
                if not source_key or not target_key or source_key == target_key:
                    continue
                if not relation_directed and target_key < source_key:
                    source, target = target, source
                    source_key, target_key = target_key, source_key

                matched_role = None
                counterpart = None
                if selection_type == "node":
                    if entity_id == source_key:
                        matched_role = "subject" if relation_directed else "participant"
                        counterpart = target
                    elif entity_id == target_key:
                        matched_role = "object" if relation_directed else "participant"
                        counterpart = source
                    else:
                        continue
                    if role != "all" and matched_role != role:
                        continue
                else:
                    if (
                        source_key != source_id or target_key != target_id
                        or predicate_id != predicate
                        or relation_directed != directed
                    ):
                        continue

                item = articles.setdefault(article_uuid, {
                    "uuid": article_uuid,
                    "title": str(event.get("_view_title") or "未命名情报"),
                    "score": float(event.get("_view_score") or 0),
                    "content_time": event.get("_view_content_time"),
                    "event_ids": set(),
                    "relation_keys": set(),
                    "predicates": Counter(),
                    "counterparts": Counter(),
                    "role_events": defaultdict(set),
                    "categories": Counter(),
                })
                relation_key = (
                    event_uuid, source_key, target_key, predicate_id, relation_directed)
                if relation_key in item["relation_keys"]:
                    continue
                item["relation_keys"].add(relation_key)
                item["event_ids"].add(event_uuid)
                item["predicates"][predicate_label] += 1
                item["categories"][category] += 1
                if matched_role:
                    item["role_events"][matched_role].add(event_uuid)
                if counterpart:
                    name = str(counterpart.get("name") or "").strip()
                    if name:
                        item["counterparts"][name] += 1

        ordered = sorted(
            articles.values(),
            key=lambda item: (
                -self._time_sort_value(item["content_time"]),
                -item["score"],
                item["uuid"],
            ),
        )
        page = ordered[offset:offset + limit]
        results = []
        role_order = ("subject", "object", "participant")
        for item in page:
            results.append({
                "uuid": item["uuid"],
                "title": item["title"],
                "score": round(item["score"], 2),
                "content_time": self._json_time(item["content_time"]),
                "event_count": len(item["event_ids"]),
                "predicates": [
                    {"label": label, "count": count}
                    for label, count in item["predicates"].most_common(6)
                ],
                "counterparts": [
                    {"name": name, "count": count}
                    for name, count in item["counterparts"].most_common(6)
                ],
                "roles": [
                    {"role": value, "event_count": len(item["role_events"][value])}
                    for value in role_order if item["role_events"].get(value)
                ],
            })

        meta = {
            "total": len(ordered),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(results) < len(ordered),
        }
        return {"intelligences": results, "meta": meta}

    def entity_graph_references(
        self, query: ViewQuery, entity_id: str,
    ) -> dict[str, Any]:
        """返回图节点背后的稳定 EntityRef，供登录用户逐个指定实体。"""
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            raise ValueError("缺少 entity_id")
        events, _ = self._load_events(query, GRAPH_EVENT_PROJECTION)
        prepared_events, country_hints = self._prepare_graph_events(events, query)
        references: dict[str, dict[str, Any]] = {}
        for event, _category, relations in prepared_events:
            article_uuid = str(event.get("intelligence_uuid") or "")
            event_uuid = str(event.get("event_uuid") or "")
            for source, target, _directed in relations:
                for entity in (source, target):
                    key, _ = self._graph_entity_key(entity, country_hints)
                    ref = entity.get("_entity_ref")
                    if key != entity_id or not isinstance(ref, dict):
                        continue
                    ref_key = str(entity.get("_entity_ref_key") or "")
                    item = references.setdefault(ref_key, {
                        "ref": dict(ref),
                        "surface": str(entity.get("name") or "").strip(),
                        "type": str(entity.get("type") or "other"),
                        "country_code": entity.get("country_code"),
                        "identity_kind": str(entity.get("_identity_kind") or "surface"),
                        "identity_method": entity.get("_identity_method"),
                        "decision_id": entity.get("_decision_id"),
                        "target_id": entity.get("_target_id"),
                        "title": str(event.get("_view_title") or "未命名情报"),
                        "content_time": self._json_time(event.get("_view_content_time")),
                        "event_ids": set(),
                    })
                    item["event_ids"].add(event_uuid)
        result = []
        for item in references.values():
            item["event_ids"] = sorted(item["event_ids"])
            result.append(item)
        result.sort(key=lambda item: (
            str(item.get("content_time") or ""), item["ref"]["intelligence_uuid"]),
            reverse=True)
        return {"entity_id": entity_id, "references": result, "total": len(result)}

    def entity_timeline_entities(
        self, query: ViewQuery, *, search: str = "", limit: int = 30,
    ) -> dict[str, Any]:
        """检索当前固定窗口内可用于时间线的实体，不受关系图规模裁剪影响。"""
        events, meta = self._load_events(query, TIMELINE_EVENT_PROJECTION)
        prepared_events, country_hints = self._prepare_graph_events(events, query)
        entities: dict[str, dict[str, Any]] = {}
        for event, _category, relations in prepared_events:
            event_uuid = str(event.get("event_uuid") or "")
            for source, target, _directed in relations:
                for entity in (source, target):
                    key, _ = self._graph_entity_key(entity, country_hints)
                    if not key:
                        continue
                    item = entities.setdefault(key, {
                        "id": key,
                        "name": self._entity_display_name(entity),
                        "type": self._entity_type_group(entity),
                        "country_code": entity.get("country_code"),
                        "identity_kind": str(entity.get("_identity_kind") or "surface"),
                        "event_ids": set(),
                        "surfaces": Counter(),
                    })
                    item["event_ids"].add(event_uuid)
                    surface = str(entity.get("name") or "").strip()
                    if surface:
                        item["surfaces"][surface] += 1

        needle = " ".join(str(search or "").casefold().split())
        values = []
        for item in entities.values():
            haystack = " ".join((
                item["id"], item["name"],
                " ".join(item["surfaces"]),
            )).casefold()
            if needle and needle not in haystack:
                continue
            values.append({
                key: value for key, value in item.items()
                if key not in {"event_ids", "surfaces"}
            } | {
                "event_count": len(item["event_ids"]),
                "surfaces": [name for name, _ in item["surfaces"].most_common(5)],
            })
        values.sort(key=lambda item: (-item["event_count"], item["name"], item["id"]))
        return {
            "entities": values[:limit],
            "meta": {"search": search, "total": len(values), "limit": limit},
        }

    def entity_timeline(
        self, query: ViewQuery, *, entity_ids: Iterable[str], max_entities: int = 8,
    ) -> dict[str, Any]:
        """把指定实体的关系投影为事件时间线；所选顺序决定分轨顺序。"""
        selected_ids = list(dict.fromkeys(
            str(value or "").strip() for value in entity_ids if str(value or "").strip()
        ))
        if not selected_ids:
            raise ValueError("至少需要一个 entity 参数")
        maximum = max(1, min(32, int(max_entities)))
        if len(selected_ids) > maximum:
            raise ValueError(f"一次最多选择 {maximum} 个实体")

        selected = set(selected_ids)
        events, meta = self._load_events(query, TIMELINE_EVENT_PROJECTION)
        prepared_events, country_hints = self._prepare_graph_events(events, query)
        entity_data: dict[str, dict[str, Any]] = {}
        timeline_events: dict[str, dict[str, Any]] = {}
        relation_seen: set[tuple] = set()

        for event, category, relations in prepared_events:
            event_uuid = str(event.get("event_uuid") or "")
            article_uuid = str(event.get("intelligence_uuid") or "")
            for source, target, directed in relations:
                source_id, _ = self._graph_entity_key(source, country_hints)
                target_id, _ = self._graph_entity_key(target, country_hints)
                focus_ids = [value for value in (source_id, target_id) if value in selected]
                if not focus_ids:
                    continue
                for entity_id, entity in ((source_id, source), (target_id, target)):
                    if entity_id not in selected:
                        continue
                    item = entity_data.setdefault(entity_id, {
                        "id": entity_id,
                        "name": self._entity_display_name(entity),
                        "type": self._entity_type_group(entity),
                        "country_code": entity.get("country_code"),
                        "identity_kind": str(entity.get("_identity_kind") or "surface"),
                        "event_ids": set(),
                    })
                    item["event_ids"].add(event_uuid)

                item = timeline_events.setdefault(event_uuid, {
                    "id": event_uuid,
                    "article_uuid": article_uuid,
                    "title": str(event.get("_view_title") or "未命名情报"),
                    "summary": str(event.get("_view_summary") or ""),
                    "score": round(float(event.get("_view_score") or 0), 2),
                    "category": category,
                    "predicate": {
                        "id": self._predicate_id(event),
                        "label": self._canonical_predicate_label(event),
                    },
                    "negated": self._is_negated(event),
                    "time": self._timeline_time(event),
                    "content_time": self._json_time(event.get("_view_content_time")),
                    "content_time_source": str(
                        event.get("_view_content_time_source") or ""),
                    "published_at": self._json_time(event.get("_view_published_at")),
                    "collected_at": self._json_time(event.get("_view_collected_at")),
                    "archived_at": self._json_time(event.get("_view_archived_at")),
                    "source_domain": str(event.get("_view_source_domain") or ""),
                    "selected_entity_ids": set(),
                    "relations": [],
                })
                item["selected_entity_ids"].update(focus_ids)
                relation_key = (
                    event_uuid, source_id, target_id, self._predicate_id(event), directed)
                if relation_key in relation_seen:
                    continue
                relation_seen.add(relation_key)
                internal = source_id in selected and target_id in selected
                for focus_id in focus_ids:
                    counterpart_id = target_id if focus_id == source_id else source_id
                    counterpart = target if focus_id == source_id else source
                    role = (
                        "participant" if not directed else
                        "subject" if focus_id == source_id else "object"
                    )
                    item["relations"].append({
                        "focus_entity_id": focus_id,
                        "counterpart_id": counterpart_id,
                        "counterpart_name": self._entity_display_name(counterpart),
                        "counterpart_type": self._entity_type_group(counterpart),
                        "role": role,
                        "directed": directed,
                        "internal": internal,
                    })

        ordered_events = []
        for item in timeline_events.values():
            item["selected_entity_ids"] = [
                value for value in selected_ids if value in item["selected_entity_ids"]]
            ordered_events.append(item)
        ordered_events.sort(key=lambda item: (
            item["time"]["timestamp"], item["id"]))

        # 仅作“共同出现/跨文章/跨来源”的透明计数，不把同文共现冒充独立印证。
        occurrence_count = sum(
            1 for item in ordered_events if len(item["selected_entity_ids"]) > 1)
        signatures: dict[tuple, dict[str, set[str]]] = defaultdict(
            lambda: {"articles": set(), "sources": set(), "entities": set()})
        for item in ordered_events:
            day = dt.datetime.fromtimestamp(
                item["time"]["timestamp"], tz=dt.timezone.utc).date().isoformat()
            for relation in item["relations"]:
                signature = (
                    day, item["predicate"]["id"], relation["counterpart_id"])
                signatures[signature]["articles"].add(item["article_uuid"])
                if item["source_domain"]:
                    signatures[signature]["sources"].add(item["source_domain"])
                signatures[signature]["entities"].add(relation["focus_entity_id"])
        corroboration = []
        for (day, predicate, counterpart_id), values in signatures.items():
            if len(values["entities"]) < 2 or len(values["articles"]) < 2:
                continue
            corroboration.append({
                "day": day,
                "predicate": predicate,
                "counterpart_id": counterpart_id,
                "entity_ids": [value for value in selected_ids if value in values["entities"]],
                "article_count": len(values["articles"]),
                "source_count": len(values["sources"]),
                "level": "cross_source" if len(values["sources"]) >= 2 else "cross_article",
            })
        entity_values = []
        for entity_id in selected_ids:
            item = entity_data.get(entity_id)
            if item is None:
                entity_values.append({
                    "id": entity_id, "name": entity_id, "type": "other",
                    "country_code": None, "identity_kind": "unknown", "event_count": 0,
                })
            else:
                entity_values.append({
                    key: value for key, value in item.items() if key != "event_ids"
                } | {"event_count": len(item["event_ids"])})
        meta = {
            "truncated": meta["truncated"],
            "selected_entity_count": len(selected_ids),
            "timeline_event_count": len(ordered_events),
            "cooccurrence_event_count": occurrence_count,
            "corroboration_count": len(corroboration),
        }
        return {
            "entities": entity_values,
            "events": ordered_events,
            "corroboration": corroboration,
            "meta": meta,
        }

    @staticmethod
    def _timeline_time(event: dict) -> dict[str, Any]:
        """优先事件明确时间；不可用时明确回退文章内容时间。"""
        time_values = event.get("time") or {}
        slots = (
            "event_time", "start_time", "effective_time", "end_time",
            "expected_start_time", "expected_end_time", "deadline",
        )
        for slot in slots:
            expression = time_values.get(slot)
            if not isinstance(expression, dict):
                continue
            normalized = str(expression.get("normalized") or "").strip()
            parsed = SituationViewEngine._normalized_time(normalized)
            if parsed is None:
                continue
            precision = str(expression.get("precision") or "").strip() or {
                4: "year", 7: "month", 10: "day", 13: "hour",
            }.get(len(normalized), "minute")
            return {
                "timestamp": parsed.timestamp(),
                "normalized": normalized,
                "precision": precision,
                "approximate": bool(expression.get("approximate")),
                "surface": str(expression.get("surface") or ""),
                "slot": slot,
                "source": "event_time",
            }
        content_time = event.get("_view_content_time")
        timestamp = SituationViewEngine._time_sort_value(content_time)
        return {
            "timestamp": timestamp,
            "normalized": SituationViewEngine._json_time(content_time),
            "precision": "datetime",
            "approximate": False,
            "surface": "",
            "slot": None,
            "source": "content_time",
        }

    @staticmethod
    def _normalized_time(value: str) -> dt.datetime | None:
        if not value:
            return None
        try:
            if re.fullmatch(r"\d{4}", value):
                return dt.datetime(int(value), 1, 1, tzinfo=dt.timezone.utc)
            if re.fullmatch(r"\d{4}-\d{2}", value):
                year, month = map(int, value.split("-"))
                calendar.monthrange(year, month)
                return dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc)
        except (ValueError, OverflowError):
            return None

    def _prepare_graph_events(self, events, query):
        self._resolve_graph_entities(events)
        prepared_events = []
        country_hints: dict[tuple[str, str], set[str]] = defaultdict(set)
        for event in events:
            category = self._category(event)
            if query.categories is not None and category not in query.categories:
                continue
            relations = self._entity_relations(event)
            if not relations:
                continue
            prepared_events.append((event, category, relations))
            for source, target, _ in relations:
                for entity in (source, target):
                    name, entity_type, country = self._entity_identity_parts(entity)
                    if name and country:
                        country_hints[(name, entity_type)].add(country)
        return prepared_events, country_hints

    def _resolve_graph_entities(self, events) -> None:
        """给派生事件快照附加非持久的图身份；解析失败时安全降级。"""
        requests: list[dict[str, Any]] = []
        entities_by_ref: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            article_uuid = str(event.get("intelligence_uuid") or "").strip()
            if not article_uuid:
                continue
            for values in (event.get("roles") or {}).values():
                for entity in values or ():
                    # Fake/in-memory repositories may reuse nested dictionaries between
                    # queries. Always clear the non-persistent projection annotations.
                    for field in (
                        "_graph_id", "_identity_method", "_target_id", "_decision_id",
                        "_display_name", "_identity_type", "_entity_ref", "_entity_ref_key",
                    ):
                        entity.pop(field, None)
                    entity["_identity_kind"] = "surface"
                    local_id = str(entity.get("local_entity_id") or "").strip()
                    if not re.fullmatch(r"ENT[1-9][0-9]*", local_id):
                        continue
                    ref = {
                        "subsystem": self.subsystem,
                        "intelligence_uuid": article_uuid,
                        "local_entity_id": local_id,
                    }
                    ref_key = f"{self.subsystem}:{article_uuid}:{local_id}"
                    entity["_entity_ref"] = ref
                    entity["_entity_ref_key"] = ref_key
                    entities_by_ref[ref_key].append(entity)
                    if len(entities_by_ref[ref_key]) == 1:
                        requests.append({
                            **ref,
                            "surface": str(entity.get("name") or "").strip(),
                            "type": str(entity.get("type") or "other"),
                            "country_code": entity.get("country_code"),
                        })

        resolved: dict[str, dict[str, Any]] = {}
        if requests and self.entity_resolver is not None:
            try:
                resolved = self.entity_resolver.resolve_many(requests)
            except Exception:
                logger.warning("实体图批量解析失败，未解析节点退回名称投影。", exc_info=True)
        elif requests and self.entity_registry is not None:
            for request in requests:
                try:
                    exact = self.entity_registry.resolve(request["surface"])
                except Exception:
                    exact = None
                if exact:
                    key = (f"{request['subsystem']}:{request['intelligence_uuid']}:"
                           f"{request['local_entity_id']}")
                    resolved[key] = {
                        "kind": "global", "target_id": exact["entity_id"],
                        "name": exact["canonical"], "type": exact.get("type"),
                        "method": "fixed_alias",
                    }

        for ref_key, entities in entities_by_ref.items():
            hit = resolved.get(ref_key)
            if not hit:
                continue
            for entity in entities:
                entity["_graph_id"] = str(hit["target_id"])
                entity["_identity_kind"] = str(hit["kind"])
                entity["_identity_method"] = str(hit.get("method") or "manual")
                entity["_target_id"] = str(hit["target_id"])
                entity["_decision_id"] = hit.get("decision_id")
                entity["_display_name"] = str(hit.get("name") or entity.get("name") or "")
                entity["_identity_type"] = str(hit.get("type") or entity.get("type") or "other")

    def _load_events(
        self, query: ViewQuery, event_projection: dict[str, int],
    ) -> tuple[list[dict], dict[str, Any]]:
        """读取固定窗口的归档头和指定态势用途的最小事件投影。"""
        archive_filter = {
            appendix_path(APPENDIX_CONTENT_TIME): {"$gte": query.start, "$lte": query.end},
            appendix_path(APPENDIX_TOTAL_SCORE): {"$gte": query.min_score},
        }
        projection = {
            "_id": 0,
            "UUID": 1,
            "EVENT_TITLE": 1,
            "INFORMANT": 1,
            appendix_path(APPENDIX_CONTENT_TIME): 1,
            appendix_path(APPENDIX_TOTAL_SCORE): 1,
        }
        if event_projection is TIMELINE_EVENT_PROJECTION:
            projection["EVENT_BRIEF"] = 1
            # 时间线卡片与情报列表使用同一组三时间口径；其它态势接口仍只读取
            # 内容主时间，避免为地图和关系图扩大 Mongo 投影。
            projection.update({
                appendix_path(APPENDIX_CONTENT_TIME_SOURCE): 1,
                appendix_path(APPENDIX_TIME_PUB): 1,
                appendix_path(APPENDIX_TIME_GOT): 1,
                appendix_path(APPENDIX_TIME_ARCHIVED): 1,
            })
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
                "content_time_source": appendix.get(APPENDIX_CONTENT_TIME_SOURCE),
                "published_at": appendix.get(APPENDIX_TIME_PUB),
                "collected_at": appendix.get(APPENDIX_TIME_GOT),
                "archived_at": appendix.get(APPENDIX_TIME_ARCHIVED),
                "score": appendix.get(APPENDIX_TOTAL_SCORE),
                "title": item.get("EVENT_TITLE"),
                "summary": re.sub(
                    r"\s+", " ", str(item.get("EVENT_BRIEF") or "")).strip()[:600],
                "source_domain": urlparse(str(item.get("INFORMANT") or "")).hostname or "",
            }
        identifiers = list(article_meta)
        event_filter: dict[str, Any] = {"intelligence_uuid": {"$in": identifiers}}
        if query.primary_only:
            event_filter["is_primary"] = True
        projection = {"_id": 0, **event_projection}
        events = list(self.event_collection.find(event_filter, projection)) if identifiers else []
        for event in events:
            data = article_meta.get(str(event.get("intelligence_uuid"))) or {}
            event["_view_content_time"] = data.get("content_time")
            event["_view_content_time_source"] = data.get("content_time_source")
            event["_view_published_at"] = data.get("published_at")
            event["_view_collected_at"] = data.get("collected_at")
            event["_view_archived_at"] = data.get("archived_at")
            event["_view_score"] = data.get("score")
            event["_view_title"] = data.get("title")
            event["_view_summary"] = data.get("summary")
            event["_view_source_domain"] = data.get("source_domain")
        return events, {
            "start": self._json_time(query.start),
            "end": self._json_time(query.end),
            "min_score": query.min_score,
            "primary_only": query.primary_only,
            "categories": sorted(query.categories) if query.categories is not None else None,
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
        if "terror" in tags:
            return "terror"
        if "crime" in tags:
            return "security"
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

    def _canonical_predicate_label(self, event: dict) -> str:
        spec = self.registry.get(self._predicate_id(event)) if self.registry else None
        label = str(getattr(spec, "label", "") or "").strip()
        return label or self._predicate_label(event)

    @staticmethod
    def _is_negated(event: dict) -> bool:
        """事件带 polarity=negated 限定词（未发生/已取消/否认）。"""
        for qualifier in event.get("qualifiers") or []:
            if (qualifier.get("type") == "polarity"
                    and qualifier.get("value") == "negated"):
                return True
        return False

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
        local_id = str((entity or {}).get("local_entity_id") or "").strip()
        if local_id:
            return f"local:{local_id}"
        name, entity_type, country = SituationViewEngine._entity_identity_parts(entity)
        return f"{entity_type}|{country}|{name}" if name else ""

    @staticmethod
    def _entity_identity_parts(entity: dict) -> tuple[str, str, str]:
        if not isinstance(entity, dict):
            return "", "", ""
        name = re.sub(r"\s+", " ", str(entity.get("name") or "").strip()).casefold()
        entity_type = str(entity.get("type") or "other").strip().casefold()
        country = str(entity.get("country_code") or "").strip().upper()
        return name, entity_type, country

    @staticmethod
    def _entity_type_group(entity: dict) -> str:
        entity_type = str(
            (entity or {}).get("_identity_type")
            or (entity or {}).get("type") or "other").strip().casefold()
        if entity_type == "country":
            return "geopolitical_entity"
        return entity_type if entity_type in ENTITY_TYPE_GROUPS - {"other"} else "other"

    @staticmethod
    def _graph_entity_key(entity: dict, country_hints) -> tuple[str, bool]:
        resolved_key = str((entity or {}).get("_graph_id") or "").strip()
        if resolved_key:
            return resolved_key, False
        name, entity_type, country = SituationViewEngine._entity_identity_parts(entity)
        if not name:
            return "", False
        inferred = False
        if not country:
            candidates = country_hints.get((name, entity_type), set())
            if len(candidates) == 1:
                country = next(iter(candidates))
                inferred = True
        return f"{entity_type}|{country}|{name}", inferred

    @staticmethod
    def _country_from_graph_key(value: str) -> str | None:
        parts = str(value or "").split("|", 2)
        return (parts[1] or None) if len(parts) == 3 else None

    @staticmethod
    def _touch_node(store, key, entity, event_uuid, article_uuid, score):
        display_name = SituationViewEngine._entity_display_name(entity)
        identity_kind = str(entity.get("_identity_kind") or "legacy")
        target_id = entity.get("_target_id")
        item = store.setdefault(key, {
            "name": display_name,
            "type": SituationViewEngine._entity_type_group(entity),
            "country_code": entity.get("country_code"),
            "identity_kind": identity_kind,
            "target_id": target_id,
            "wiki_name": display_name if identity_kind == "global" else None,
            "reference_keys": set(),
            "surfaces": Counter(),
            "event_ids": set(),
            "article_ids": set(),
            "score_max": 0.0,
        })
        item["event_ids"].add(event_uuid)
        item["article_ids"].add(article_uuid)
        surface = str(entity.get("name") or "").strip()
        if surface:
            item["surfaces"][surface] += 1
        ref_key = str(entity.get("_entity_ref_key") or "")
        if ref_key:
            item["reference_keys"].add(ref_key)
        item["score_max"] = max(item["score_max"], score)
        if not item.get("country_code") and entity.get("country_code"):
            item["country_code"] = entity.get("country_code")

    @staticmethod
    def _entity_display_name(entity: dict) -> str:
        return str((entity or {}).get("_display_name") or (entity or {}).get("name") or "").strip()

    @staticmethod
    def _json_time(value):
        if isinstance(value, dt.datetime):
            return value.isoformat()
        return value

    @staticmethod
    def _time_sort_value(value) -> float:
        if not isinstance(value, dt.datetime):
            return 0.0
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.timestamp()
