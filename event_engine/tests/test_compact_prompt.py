"""精简目录必须能无损展开为原 Registry，不能因去重丢失角色或 Frame。"""

from dataclasses import replace

import pytest

from event_engine.core.registry import PredicateRegistry
from event_engine.domains import registry_for
from event_engine.extraction import build_compact_predicate_catalog


def _expand_catalog(text):
    rows = {}
    common = set()
    frames = ()
    for line in text.splitlines():
        if line.startswith("公共可选角色："):
            value = line.split("：", 1)[1]
            common = set(value.split(",")) if value != "无" else set()
        elif line.startswith("### FRAME="):
            value = line.removeprefix("### FRAME=")
            frames = tuple(value.split(" | ")) if value != "open" else ()
        elif line.startswith("- "):
            predicate_id, label, definition, role_text = line[2:].split(" | ")
            tokens = role_text.removeprefix("roles=")
            tokens = tokens.split(",") if tokens != "无" else []
            assert predicate_id not in rows
            rows[predicate_id] = (
                label, definition, frames,
                common | {token[:-1] for token in tokens},
                {token[:-1] for token in tokens if token.endswith("+")},
            )
    return rows


def _assert_roundtrip(registry):
    expanded = _expand_catalog(build_compact_predicate_catalog(registry))
    assert set(expanded) == set(registry)
    for predicate_id, spec in registry.items():
        frames = spec.allowed_frames or ((spec.frame,) if spec.frame else ())
        assert expanded[predicate_id] == (
            spec.label or predicate_id,
            spec.definition or spec.label or predicate_id,
            tuple("/".join((f.dynamics.value, f.topology.value, f.agency.value)) for f in frames),
            set(spec.role_groups),
            set(spec.required_roles),
        )


@pytest.mark.parametrize("domains", [
    ("news",), ("industry",), ("financial",), ("news", "industry", "financial"),
])
def test_compact_catalog_preserves_all_registry_information(domains):
    _assert_roundtrip(registry_for(*domains))


def test_common_role_extraction_preserves_predicate_specific_requirements():
    source = registry_for("news")
    registry = PredicateRegistry({
        "attack": replace(source["attack"], required_roles=("target",)),
        "investigate": source["investigate"],
    })
    _assert_roundtrip(registry)
    common_line = build_compact_predicate_catalog(registry).splitlines()[0]
    assert "target" not in common_line
    expanded = _expand_catalog(build_compact_predicate_catalog(registry))
    assert expanded["attack"][-1] == {"target"}
    assert expanded["investigate"][-1] == set()


def test_frame_alternative_order_and_missing_frame_survive_grouping():
    source = registry_for("news")
    attack, investigate = source["attack"], source["investigate"]
    variants = (source["possess"].frame, attack.frame)
    registry = PredicateRegistry({
        "attack": replace(attack, allowed_frames=variants),
        "investigate": replace(investigate, allowed_frames=tuple(reversed(variants))),
        "exist": replace(source["exist"], frame=None),
    })
    _assert_roundtrip(registry)


def test_empty_and_single_predicate_catalogs():
    assert _expand_catalog(build_compact_predicate_catalog(PredicateRegistry())) == {}
    source = registry_for("news")
    _assert_roundtrip(PredicateRegistry({"attack": source["attack"]}))


def test_no_common_roles_and_missing_labels():
    source = registry_for("news")
    attack = source["attack"]
    exist = source["exist"]
    registry = PredicateRegistry({
        "attack": replace(
            attack, role_groups={r: attack.role_groups[r] for r in ("actor", "target", "instrument")},
            label="", definition="",
        ),
        "exist": replace(exist, role_groups={"subject": exist.role_groups["subject"]}),
    })
    assert build_compact_predicate_catalog(registry).startswith("公共可选角色：无\n")
    _assert_roundtrip(registry)
