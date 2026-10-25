from pathlib import Path

import pytest

from OperationsDashboard.dashboard import (
    DashboardConfigError,
    layout_signature,
    load_config,
    panel_ids,
)


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "dashboard.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_recursive_layout_is_loaded(tmp_path):
    config = load_config(write_config(tmp_path, """
session: test-dashboard
windows:
  wall:
    horizontal:
      - overview
      - vertical: [web-log, crawler-log]
"""))
    assert config.session == "test-dashboard"
    assert panel_ids(config.windows["wall"]) == [
        "overview", "web-log", "crawler-log"]


@pytest.mark.parametrize("node", [
    "horizontal: [overview]",
    "horizontal: [overview, web-log, crawler-log]",
    "diagonal: [overview, web-log]",
])
def test_split_must_be_binary_and_known(tmp_path, node):
    indented = "\n".join(f"    {line}" for line in node.splitlines())
    path = write_config(tmp_path, f"windows:\n  wall:\n{indented}\n")
    with pytest.raises(DashboardConfigError):
        load_config(path)


def test_signature_changes_with_structure():
    first = {"horizontal": ["overview", "web-log"]}
    second = {"vertical": ["overview", "web-log"]}
    assert panel_ids(first) == panel_ids(second)
    assert layout_signature(first) != layout_signature(second)
