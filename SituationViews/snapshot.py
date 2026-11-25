"""实体关系图的完整文本快照与滚动窗口增量快照。"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import threading
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Iterable

from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY
from SituationViews.engine import SituationViewEngine, ViewQuery


logger = logging.getLogger(__name__)
FORMAT_VERSION = "iis-entity-graph-snapshot/1.0"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: Any) -> None:
    _atomic_write_text(
        path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _edge_key(edge: dict[str, Any]) -> str:
    source = str(edge.get("source") or "")
    target = str(edge.get("target") or "")
    directed = bool(edge.get("directed", True))
    if not directed and target < source:
        source, target = target, source
    predicate = str(edge.get("predicate") or "")
    predicate_label = str(edge.get("predicate_label") or "")
    return json.dumps(
        [source, target, predicate, predicate_label, directed], ensure_ascii=False,
        separators=(",", ":"))


def _node_signature(node: dict[str, Any]) -> str:
    return json.dumps([
        str(node.get("name") or ""),
        str(node.get("type") or "other"),
        str(node.get("country_code") or ""),
        str(node.get("identity_kind") or "surface"),
        str(node.get("target_id") or ""),
    ], ensure_ascii=False, separators=(",", ":"))


def stable_short_ids(node_ids: Iterable[str]) -> dict[str, str]:
    """真实图 ID -> 跨快照稳定短 ID；冲突时自动延长摘要。"""
    result: dict[str, str] = {}
    owners: dict[str, str] = {}
    for node_id in sorted(set(str(value) for value in node_ids)):
        prefix = "E" if node_id.startswith("ent_") else (
            "C" if node_id.startswith("ctx_") else "S")
        digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()
        length = 10
        short = f"{prefix}_{digest[:length]}"
        while short in owners and owners[short] != node_id:
            length += 2
            short = f"{prefix}_{digest[:length]}"
        owners[short] = node_id
        result[node_id] = short
    return result


def _adjacency(node_ids: Iterable[str], edges: Iterable[dict[str, Any]]):
    result: dict[str, set[str]] = {
        str(node_id): set() for node_id in node_ids}
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source in result and target in result:
            result[source].add(target)
            result[target].add(source)
    return result


def _connected_components(adjacency: dict[str, set[str]]) -> list[set[str]]:
    seen: set[str] = set()
    groups: list[set[str]] = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        group = {start}
        seen.add(start)
        pending = [start]
        while pending:
            current = pending.pop()
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    group.add(neighbor)
                    pending.append(neighbor)
        groups.append(group)
    return groups


def _communities(nodes: dict[str, dict[str, Any]],
                 edges: list[dict[str, Any]]) -> list[set[str]]:
    """仅用于文本排序；networkx 不可用时退回连通分量。"""
    adjacency = _adjacency(nodes, edges)
    try:
        import networkx as nx
        graph = nx.Graph()
        graph.add_nodes_from(nodes)
        for edge in edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source not in nodes or target not in nodes:
                continue
            weight = max(1, int(edge.get("support") or 1))
            if graph.has_edge(source, target):
                graph[source][target]["weight"] += weight
            else:
                graph.add_edge(source, target, weight=weight)
        groups = list(nx.community.louvain_communities(
            graph, weight="weight", resolution=1, seed=42))
    except (ImportError, AttributeError):
        groups = _connected_components(adjacency)
    return sorted(
        (set(group) for group in groups),
        key=lambda group: (-len(group), min(group) if group else ""),
    )


def _edge_line(edge: dict[str, Any], short_ids: dict[str, str]) -> str:
    label = str(edge.get("predicate_label") or edge.get("predicate") or "关联")
    support = int(edge.get("support") or 0)
    suffix = f" ×{support}" if support > 1 else ""
    source = short_ids[str(edge["source"])]
    target = short_ids[str(edge["target"])]
    if not edge.get("directed", True):
        return f"{source} --[{label}{suffix}]-- {target}"
    return f"{source} --[{label}{suffix}]--> {target}"


def _edge_sort_key(edge: dict[str, Any], short_ids: dict[str, str]):
    return (
        -int(edge.get("support") or 0),
        str(edge.get("predicate_label") or edge.get("predicate") or ""),
        short_ids[str(edge.get("source") or "")],
        short_ids[str(edge.get("target") or "")],
    )


def _render_groups(*, nodes: dict[str, dict[str, Any]],
                   edges: list[dict[str, Any]],
                   short_ids: dict[str, str],
                   groups: list[set[str]],
                   core_ids: set[str] | None = None) -> list[str]:
    core_ids = core_ids or set()
    group_of = {
        node_id: index for index, group in enumerate(groups) for node_id in group}
    internal: dict[int, list[dict[str, Any]]] = defaultdict(list)
    cross: list[dict[str, Any]] = []
    for edge in edges:
        source_group = group_of.get(str(edge.get("source") or ""))
        target_group = group_of.get(str(edge.get("target") or ""))
        if source_group is None or target_group is None:
            continue
        if source_group == target_group:
            internal[source_group].append(edge)
        else:
            cross.append(edge)

    lines: list[str] = []
    for index, group in enumerate(groups):
        group_edges = sorted(
            internal[index], key=lambda item: _edge_sort_key(item, short_ids))
        cross_count = sum(
            group_of.get(str(edge.get("source") or "")) == index
            or group_of.get(str(edge.get("target") or "")) == index
            for edge in cross)
        core_count = len(group.intersection(core_ids))
        core_label = f"，{core_count} 核心" if core_ids else ""
        lines.append(
            f"## 团块 G{index + 1:03d}（{len(group)} 节点，"
            f"{len(group_edges)} 团内边，{cross_count} 跨团边{core_label}）")
        lines.append("节点：")
        for node_id in sorted(
                group,
                key=lambda value: (
                    value not in core_ids,
                    -int(nodes[value].get("importance") or 0),
                    str(nodes[value].get("name") or ""),
                    short_ids[value])):
            node = nodes[node_id]
            marker = "* " if node_id in core_ids else "  "
            name = str(node.get("name") or "(未命名)").replace("\n", " ")
            entity_type = str(node.get("type") or "other")
            lines.append(
                f"{marker}{short_ids[node_id]} {name}（{entity_type}）")
        lines.append("关系：")
        lines.extend(_edge_line(edge, short_ids) for edge in group_edges)
        lines.append("")
    if cross:
        lines.append("## 跨团关系")
        lines.extend(
            _edge_line(edge, short_ids)
            for edge in sorted(cross, key=lambda item: _edge_sort_key(item, short_ids)))
        lines.append("")
    return lines


class EntityGraphSnapshotDumper:
    """生成完整/增量文本文件，并以轻量状态比较相邻两次七日图。"""

    def __init__(self, graph_provider: Callable[[dt.datetime], dict[str, Any]], *,
                 export_dir: str | Path, state_file: str | Path,
                 context_hops: int = 2, community_context_max_nodes: int = 120,
                 retention_days: int = 30):
        self.graph_provider = graph_provider
        self.export_dir = Path(export_dir)
        self.state_file = Path(state_file)
        self.context_hops = max(0, int(context_hops))
        self.community_context_max_nodes = max(
            1, int(community_context_max_nodes))
        self.retention_days = max(1, int(retention_days))
        self._lock = threading.Lock()

    def dump(self, now: dt.datetime | None = None) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"skipped": True, "reason": "already_running"}
        try:
            current_time = now or dt.datetime.now(dt.timezone.utc)
            if current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=dt.timezone.utc)
            graph = self.graph_provider(current_time)
            result = self._dump_graph(graph, current_time)
            self._cleanup(current_time)
            return result
        finally:
            self._lock.release()

    def _dump_graph(self, graph: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
        nodes = {
            str(item.get("id") or ""): dict(item)
            for item in graph.get("nodes") or ()
            if str(item.get("id") or "")
        }
        edges = [
            dict(item) for item in graph.get("edges") or ()
            if str(item.get("source") or "") in nodes
            and str(item.get("target") or "") in nodes
        ]
        short_ids = stable_short_ids(nodes)
        groups = _communities(nodes, edges)
        previous = self._load_state()
        delta = self._delta(previous, nodes, edges)
        selected = self._incremental_context(
            nodes, edges, groups, delta["core_ids"])
        selected_edges = [
            edge for edge in edges
            if str(edge.get("source") or "") in selected
            and str(edge.get("target") or "") in selected
        ]
        selected_groups = [group.intersection(selected) for group in groups
                           if group.intersection(selected)]

        stamp = now.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        full_path = self.export_dir / f"entity_graph_full_{stamp}.txt"
        incremental_path = self.export_dir / f"entity_graph_incremental_{stamp}.txt"
        mapping_path = self.export_dir / f"entity_graph_ids_{stamp}.json"
        meta = dict(graph.get("meta") or {})
        full_lines = [
            "# IIS 实体关系图完整文本快照",
            f"生成时间：{now.astimezone(dt.timezone.utc).isoformat()}",
            f"时间窗口：{meta.get('start')} → {meta.get('end')}",
            f"节点：{len(nodes)}；边：{len(edges)}；团块：{len(groups)}；"
            f"截断：{'是' if meta.get('truncated') else '否'}",
            "说明：短 ID 跨快照稳定；括号内为图上类型；×N 为关系支撑次数。",
            "",
        ]
        full_lines.extend(_render_groups(
            nodes=nodes, edges=edges, short_ids=short_ids, groups=groups))

        incremental_lines = [
            "# IIS 实体关系图增量文本快照",
            f"生成时间：{now.astimezone(dt.timezone.utc).isoformat()}",
            f"时间窗口：{meta.get('start')} → {meta.get('end')}",
            f"基线：{previous.get('generated_at') or '无（首次运行）'}",
            f"新增节点：{len(delta['added_nodes'])}；移除节点：{len(delta['removed_nodes'])}；"
            f"变化节点：{len(delta['changed_nodes'])}；新增边：{len(delta['added_edges'])}；"
            f"移除边：{len(delta['removed_edges'])}",
            f"本次核心节点：{len(delta['core_ids'])}；带上下文节点：{len(selected)}；"
            f"上下文跳数：{self.context_hops}",
            "说明：* 表示 CORE；其余节点仅作为当前七日图上下文。",
            "",
        ]
        if selected:
            incremental_lines.extend(_render_groups(
                nodes={node_id: nodes[node_id] for node_id in selected},
                edges=selected_edges,
                short_ids=short_ids,
                groups=selected_groups,
                core_ids=set(delta["core_ids"]),
            ))
        else:
            incremental_lines.append("本次没有结构增量。")
        self._append_removed(incremental_lines, delta, previous)

        _atomic_write_text(full_path, "\n".join(full_lines))
        _atomic_write_text(incremental_path, "\n".join(incremental_lines))
        _atomic_write_json(mapping_path, {
            "format": FORMAT_VERSION,
            "generated_at": now.astimezone(dt.timezone.utc).isoformat(),
            "nodes": {
                short_ids[node_id]: {
                    "graph_id": node_id,
                    "name": nodes[node_id].get("name"),
                    "type": nodes[node_id].get("type"),
                }
                for node_id in sorted(nodes)
            },
        })
        _atomic_write_json(
            self.state_file, self._state(graph, nodes, edges, short_ids, now))
        logger.info(
            "实体图快照完成：nodes=%d edges=%d core=%d context=%d full=%s incremental=%s",
            len(nodes), len(edges), len(delta["core_ids"]), len(selected),
            full_path, incremental_path)
        return {
            "full_path": str(full_path),
            "incremental_path": str(incremental_path),
            "mapping_path": str(mapping_path),
            "nodes": len(nodes),
            "edges": len(edges),
            "core_nodes": len(delta["core_ids"]),
            "context_nodes": len(selected),
            "first_run": not bool(previous),
        }

    @staticmethod
    def _state(graph, nodes, edges, short_ids, now):
        return {
            "format": FORMAT_VERSION,
            "generated_at": now.astimezone(dt.timezone.utc).isoformat(),
            "meta": dict(graph.get("meta") or {}),
            "nodes": {node_id: _node_signature(node)
                      for node_id, node in sorted(nodes.items())},
            "node_details": {node_id: {
                "short_id": short_ids[node_id],
                "name": str(node.get("name") or ""),
                "type": str(node.get("type") or "other"),
            } for node_id, node in sorted(nodes.items())},
            "edges": {_edge_key(edge): {
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
                "source_short_id": short_ids[str(edge.get("source") or "")],
                "target_short_id": short_ids[str(edge.get("target") or "")],
            } for edge in edges},
        }

    def _load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
            return value if value.get("format") == FORMAT_VERSION else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError, AttributeError):
            return {}

    @staticmethod
    def _delta(previous, nodes, edges):
        current_nodes = set(nodes)
        previous_nodes = set((previous.get("nodes") or {}))
        current_edges = {_edge_key(edge): edge for edge in edges}
        previous_edges = previous.get("edges") or {}
        added_nodes = current_nodes - previous_nodes
        removed_nodes = previous_nodes - current_nodes
        changed_nodes = {
            node_id for node_id in current_nodes.intersection(previous_nodes)
            if (previous.get("nodes") or {}).get(node_id) != _node_signature(nodes[node_id])
        }
        added_edge_keys = set(current_edges) - set(previous_edges)
        removed_edge_keys = set(previous_edges) - set(current_edges)
        core_ids = set(added_nodes).union(changed_nodes)
        for key in added_edge_keys:
            core_ids.add(str(current_edges[key].get("source") or ""))
            core_ids.add(str(current_edges[key].get("target") or ""))
        for key in removed_edge_keys:
            item = previous_edges[key]
            for node_id in (str(item.get("source") or ""),
                            str(item.get("target") or "")):
                if node_id in current_nodes:
                    core_ids.add(node_id)
        if not previous:
            core_ids = set(current_nodes)
        return {
            "added_nodes": sorted(added_nodes),
            "removed_nodes": sorted(removed_nodes),
            "changed_nodes": sorted(changed_nodes),
            "added_edges": sorted(added_edge_keys),
            "removed_edges": sorted(removed_edge_keys),
            "core_ids": sorted(core_ids),
        }

    def _incremental_context(self, nodes, edges, groups, core_ids):
        core = set(core_ids).intersection(nodes)
        if not core:
            return set()
        adjacency = _adjacency(nodes, edges)
        selected = set(core)
        pending = deque((node_id, 0) for node_id in core)
        while pending:
            node_id, depth = pending.popleft()
            if depth >= self.context_hops:
                continue
            for neighbor in adjacency[node_id]:
                if neighbor not in selected:
                    selected.add(neighbor)
                    pending.append((neighbor, depth + 1))
        # 小团块整体加入，使文本保留与网页相近的“一团”观感；大团块仍受跳数限制。
        for group in groups:
            if group.intersection(core) and len(group) <= self.community_context_max_nodes:
                selected.update(group)
        return selected

    @staticmethod
    def _append_removed(lines, delta, previous):
        if delta["removed_nodes"]:
            lines.extend(("", "## 滚出窗口的节点"))
            details = previous.get("node_details") or {}
            for node_id in delta["removed_nodes"]:
                item = details.get(node_id) or {}
                lines.append(
                    f"- {item.get('short_id') or node_id} "
                    f"{item.get('name') or ''}（{item.get('type') or 'unknown'}）")
        if delta["removed_edges"]:
            lines.extend(("", "## 滚出窗口的关系"))
            previous_edges = previous.get("edges") or {}
            for key in delta["removed_edges"]:
                item = previous_edges.get(key) or {}
                lines.append(
                    f"- {item.get('source_short_id') or item.get('source') or '?'} -> "
                    f"{item.get('target_short_id') or item.get('target') or '?'}")

    def _cleanup(self, now: dt.datetime) -> None:
        cutoff = now.timestamp() - self.retention_days * 86400
        try:
            for path in self.export_dir.glob("entity_graph_*"):
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
        except OSError:
            logger.warning("清理过期实体图快照失败。", exc_info=True)


def create_snapshot_dumper(*, config, domain_resources,
                           entity_resolution_service=None,
                           entity_alias_service=None,
                           export_root: str | Path,
                           data_root: str | Path) -> EntityGraphSnapshotDumper | None:
    situation_settings = config.get(
        "intelligence_hub_web_service.situation_views", {}) or {}
    if not situation_settings.get("enabled", True):
        return None
    settings = config.get(
        "intelligence_hub_web_service.situation_views.snapshot", {}) or {}
    if not settings.get("enabled", True):
        return None
    dashboard = config.get(
        "intelligence_hub_web_service.situation_views.dashboard", {}) or {}
    domain_name = str(dashboard.get("domain_name") or "").strip()
    context = domain_resources.resolve(domain_name)
    if context is None:
        raise ValueError(f"实体图快照领域不存在：{domain_name}")
    engine = SituationViewEngine(
        context.event_v4_intelligence_collection,
        context.event_v4_event_collection,
        context.event_registry or DEFAULT_EVENT_REGISTRY,
        domain_name=context.name,
        entity_resolver=entity_resolution_service,
        entity_registry=entity_alias_service,
    )

    def graph_provider(end: dt.datetime):
        query = ViewQuery(
            start=end - dt.timedelta(days=int(dashboard.get("window_days", 7))),
            end=end,
            min_score=float(dashboard.get("min_score", 6.0)),
            primary_only=bool(dashboard.get("primary_only", True)),
            max_articles=int(dashboard.get("max_articles", 0)),
        )
        return engine.entity_graph(
            query,
            min_support=int(dashboard.get("min_support", 2)),
            max_nodes=int(dashboard.get("max_nodes", 0)),
            max_edges=int(dashboard.get("max_edges", 0)),
            keep_high_score_singletons=bool(
                dashboard.get("keep_high_score_singletons", True)),
        )

    return EntityGraphSnapshotDumper(
        graph_provider,
        export_dir=Path(export_root) / str(
            settings.get("directory") or "entity_graph_snapshots"),
        state_file=Path(data_root) / str(
            settings.get("state_file") or "SituationViews/entity_graph_snapshot.json"),
        context_hops=int(settings.get("context_hops", 2)),
        community_context_max_nodes=int(
            settings.get("community_context_max_nodes", 120)),
        retention_days=int(settings.get("retention_days", 30)),
    )
