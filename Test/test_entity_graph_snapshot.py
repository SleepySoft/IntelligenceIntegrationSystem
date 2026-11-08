"""实体关系图完整/增量文字快照测试。"""

from __future__ import annotations

import datetime as dt
import json

from SituationViews.snapshot import EntityGraphSnapshotDumper, stable_short_ids


UTC = dt.timezone.utc


def _node(node_id, name, entity_type="organization", importance=1):
    return {
        "id": node_id, "name": name, "type": entity_type,
        "identity_kind": "surface", "importance": importance,
    }


def _edge(source, target, predicate="cooperate", label="合作", support=1):
    return {
        "source": source, "target": target, "predicate": predicate,
        "predicate_label": label, "directed": True, "support": support,
    }


def _graph(nodes, edges, end):
    return {
        "nodes": nodes, "edges": edges,
        "meta": {
            "start": (end - dt.timedelta(days=7)).isoformat(),
            "end": end.isoformat(), "truncated": False,
        },
    }


def test_stable_short_ids_do_not_depend_on_snapshot_order():
    first = stable_short_ids(["surface-b", "ent_a", "ctx_c"])
    second = stable_short_ids(["ctx_c", "surface-b", "ent_a"])

    assert first == second
    assert first["ent_a"].startswith("E_")
    assert first["ctx_c"].startswith("C_")
    assert first["surface-b"].startswith("S_")


def test_first_dump_uses_full_graph_as_incremental_baseline(tmp_path):
    now = dt.datetime(2026, 11, 8, 3, tzinfo=UTC)
    graph = _graph(
        [_node("a", "甲"), _node("b", "乙")], [_edge("a", "b")], now)
    dumper = EntityGraphSnapshotDumper(
        lambda _: graph, export_dir=tmp_path / "exports",
        state_file=tmp_path / "state.json")

    result = dumper.dump(now)
    incremental = (tmp_path / "exports" /
                   "entity_graph_incremental_20261108T030000Z.txt").read_text("utf-8")

    assert result["first_run"] is True
    assert result["core_nodes"] == 2
    assert "新增节点：2" in incremental
    assert incremental.count("* S_") == 2
    assert "--[合作]-->" in incremental
    assert json.loads((tmp_path / "state.json").read_text("utf-8"))["format"]


def test_second_dump_contains_only_changed_core_and_current_context(tmp_path):
    first_time = dt.datetime(2026, 11, 8, 3, tzinfo=UTC)
    second_time = first_time + dt.timedelta(days=1)
    graphs = [
        _graph(
            [_node("a", "甲"), _node("b", "乙"), _node("x", "无关")],
            [_edge("a", "b")], first_time),
        _graph(
            [_node("a", "甲"), _node("b", "乙"), _node("c", "丙"),
             _node("x", "无关")],
            [_edge("a", "b"), _edge("b", "c")], second_time),
    ]
    dumper = EntityGraphSnapshotDumper(
        lambda _: graphs.pop(0), export_dir=tmp_path / "exports",
        state_file=tmp_path / "state.json", context_hops=2)
    dumper.dump(first_time)

    result = dumper.dump(second_time)
    incremental = (tmp_path / "exports" /
                   "entity_graph_incremental_20261109T030000Z.txt").read_text("utf-8")

    assert result["first_run"] is False
    assert result["core_nodes"] == 2  # 新节点 c + 新边的既有端点 b
    assert "丙" in incremental and "乙" in incremental and "甲" in incremental
    assert "无关" not in incremental
    assert incremental.count("* S_") == 2


def test_unchanged_graph_emits_empty_increment(tmp_path):
    now = dt.datetime(2026, 11, 8, 3, tzinfo=UTC)
    graph = _graph(
        [_node("a", "甲"), _node("b", "乙")], [_edge("a", "b")], now)
    dumper = EntityGraphSnapshotDumper(
        lambda _: graph, export_dir=tmp_path / "exports",
        state_file=tmp_path / "state.json")
    dumper.dump(now)

    result = dumper.dump(now + dt.timedelta(days=1))
    incremental = (tmp_path / "exports" /
                   "entity_graph_incremental_20261109T030000Z.txt").read_text("utf-8")

    assert result["core_nodes"] == 0
    assert "本次没有结构增量" in incremental


def test_removed_nodes_and_edges_are_recorded(tmp_path):
    first_time = dt.datetime(2026, 11, 8, 3, tzinfo=UTC)
    graphs = [
        _graph([_node("a", "甲"), _node("b", "乙")],
               [_edge("a", "b")], first_time),
        _graph([_node("a", "甲")], [], first_time + dt.timedelta(days=1)),
    ]
    dumper = EntityGraphSnapshotDumper(
        lambda _: graphs.pop(0), export_dir=tmp_path / "exports",
        state_file=tmp_path / "state.json")
    dumper.dump(first_time)
    dumper.dump(first_time + dt.timedelta(days=1))
    incremental = (tmp_path / "exports" /
                   "entity_graph_incremental_20261109T030000Z.txt").read_text("utf-8")

    assert "滚出窗口的节点" in incremental
    assert "乙（organization）" in incremental
    assert "滚出窗口的关系" in incremental
