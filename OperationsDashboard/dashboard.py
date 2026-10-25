#!/usr/bin/env python3
"""从极简 YAML 布局创建和管理 IIS tmux 运维仪表盘。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


COMPONENT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = COMPONENT_DIR.parent
PANELS_DIR = COMPONENT_DIR / "panels"
DEFAULT_CONFIG = COMPONENT_DIR / "dashboard.yaml"
USER_CONFIG = Path(os.environ.get(
    "IIS_CONFIG_DIR", PROJECT_DIR / "_config")).expanduser().resolve() / "dashboard.yaml"
DEFAULT_STATE = Path(os.environ.get(
    "IIS_DATA_DIR", PROJECT_DIR / "_data")).expanduser().resolve() / "dashboard-layouts.json"
ENTRYPOINT = PROJECT_DIR / "Scripts" / "iis_dashboard.sh"

NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SPLIT_DIRECTIONS = {"horizontal": "-h", "vertical": "-v"}


class DashboardConfigError(ValueError):
    """仪表盘配置不合法。"""


@dataclass(frozen=True)
class DashboardConfig:
    session: str
    windows: dict[str, Any]
    path: Path


def resolve_config_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get("IIS_DASHBOARD_CONFIG", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return USER_CONFIG if USER_CONFIG.exists() else DEFAULT_CONFIG


def resolve_state_path() -> Path:
    configured = os.environ.get("IIS_DASHBOARD_STATE", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_STATE


def find_panel(panel_id: str) -> Path:
    if not isinstance(panel_id, str) or not NAME_RE.fullmatch(panel_id):
        raise DashboardConfigError(f"非法面板名称：{panel_id!r}")
    for suffix in ("", ".sh", ".py"):
        candidate = PANELS_DIR / f"{panel_id}{suffix}"
        if candidate.is_file():
            return candidate
    raise DashboardConfigError(
        f"面板 {panel_id!r} 不存在；请在 {PANELS_DIR} 中添加同名脚本")


def validate_node(node: Any, location: str) -> None:
    if isinstance(node, str):
        find_panel(node)
        return
    if not isinstance(node, dict) or len(node) != 1:
        raise DashboardConfigError(
            f"{location} 必须是面板名称，或只含 horizontal/vertical 的对象")
    direction, children = next(iter(node.items()))
    if direction not in SPLIT_DIRECTIONS:
        raise DashboardConfigError(f"{location} 使用了未知拆分方向 {direction!r}")
    if not isinstance(children, list) or len(children) != 2:
        raise DashboardConfigError(f"{location}.{direction} 必须正好包含两个子项")
    validate_node(children[0], f"{location}.{direction}[0]")
    validate_node(children[1], f"{location}.{direction}[1]")


def load_config(path: Path) -> DashboardConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DashboardConfigError(f"配置文件不存在：{path}") from exc
    except yaml.YAMLError as exc:
        raise DashboardConfigError(f"YAML 解析失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise DashboardConfigError("配置根节点必须是对象")
    unknown = set(raw) - {"session", "windows"}
    if unknown:
        raise DashboardConfigError(f"未知配置字段：{', '.join(sorted(unknown))}")
    session = raw.get("session", "iis-monitor")
    if not isinstance(session, str) or not NAME_RE.fullmatch(session):
        raise DashboardConfigError("session 只能包含字母、数字、下划线和连字符")
    windows = raw.get("windows")
    if not isinstance(windows, dict) or not windows:
        raise DashboardConfigError("windows 必须是非空对象")
    if len(windows) > 9:
        raise DashboardConfigError("最多支持 9 个窗口，以便使用 F1-F9 快速切换")
    for name, node in windows.items():
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise DashboardConfigError(f"非法窗口名称：{name!r}")
        validate_node(node, f"windows.{name}")
    return DashboardConfig(session=session, windows=windows, path=path)


def panel_ids(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    children = next(iter(node.values()))
    return panel_ids(children[0]) + panel_ids(children[1])


def layout_signature(node: Any) -> str:
    canonical = json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_layout_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_layout_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def tmux(*args: str, capture: bool = False, check: bool = True) -> str:
    completed = subprocess.run(
        ["tmux", *args], check=check, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None)
    return completed.stdout.strip() if capture else ""


def session_exists(session: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", session],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def panel_command(panel_id: str) -> str:
    path = find_panel(panel_id)
    if path.suffix == ".sh":
        parts = ["/usr/bin/env", "bash", str(path)]
    elif path.suffix == ".py":
        parts = [sys.executable, str(path)]
    else:
        parts = [str(path)]
    return shlex.join(parts)


def build_node(session: str, window: str, pane: str, node: Any) -> None:
    # tmux 的全局 pane id（如 %3）本身就是稳定 target；拼接窗口名会形成非法 target。
    target = pane
    if isinstance(node, str):
        tmux("respawn-pane", "-k", "-t", target, panel_command(node))
        tmux("select-pane", "-t", target, "-T", node)
        return
    direction, children = next(iter(node.items()))
    new_pane = tmux(
        "split-window", "-d", SPLIT_DIRECTIONS[direction], "-p", "50",
        "-P", "-F", "#{pane_id}", "-t", target, "-c", str(PROJECT_DIR),
        "exec sleep infinity", capture=True)
    build_node(session, window, pane, children[0])
    build_node(session, window, new_pane, children[1])


def apply_saved_layout(config: DashboardConfig, window: str, node: Any) -> None:
    record = load_layout_state(resolve_state_path()).get(window)
    if not isinstance(record, dict) or record.get("signature") != layout_signature(node):
        return
    layout = record.get("layout")
    if not isinstance(layout, str) or not layout:
        return
    result = subprocess.run(
        ["tmux", "select-layout", "-t", f"{config.session}:{window}", layout],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        print(f"警告：窗口 {window} 的已保存布局无法恢复，已使用 50% 基础布局。",
              file=sys.stderr)


def configure_session(config: DashboardConfig) -> None:
    session = config.session
    options = [
        ("mouse", "on"), ("history-limit", "50000"), ("status-interval", "2"),
        ("pane-border-status", "top"),
        ("pane-border-format", "#[fg=#565f89] #{pane_title} "),
        ("pane-active-border-style", "fg=#7aa2f7"),
        ("status-style", "bg=#1f2335,fg=#c0caf5"),
        ("window-status-current-style", "bg=#7aa2f7,fg=#1f2335,bold"),
        ("status-left", "#[bold] IIS #[default]"),
        ("status-right", "#[fg=#9ece6a]%F %T #[default] F1-F9 windows | F10 save | Alt-z zoom "),
    ]
    for name, value in options:
        tmux("set-option", "-t", session, name, value)
    for index, window in enumerate(config.windows, start=1):
        tmux("bind-key", "-n", f"F{index}", "select-window", "-t", f"{session}:{window}")
        tmux("bind-key", "-n", f"M-{index}", "select-window", "-t", f"{session}:{window}")
    save_current = shlex.join(
        [str(ENTRYPOINT), "save-layout", "--window", "#{window_name}"])
    tmux("bind-key", "-n", "F10", "run-shell", save_current)
    tmux("bind-key", "-n", "M-z", "resize-pane", "-Z")


def require_tmux() -> None:
    if shutil.which("tmux") is None:
        raise RuntimeError("缺少 tmux；请先安装：sudo apt-get install tmux")


def start_dashboard(config: DashboardConfig) -> None:
    require_tmux()
    if session_exists(config.session):
        return
    created = False
    try:
        first = True
        for window, node in config.windows.items():
            if first:
                # systemd 在没有客户端时创建会话；给复杂布局足够的初始拆分空间。
                tmux("new-session", "-d", "-x", "240", "-y", "60",
                     "-s", config.session, "-n", window, "-c", str(PROJECT_DIR),
                     "exec sleep infinity")
                first = False
                created = True
            else:
                tmux("new-window", "-d", "-t", f"{config.session}:", "-n", window,
                     "-c", str(PROJECT_DIR), "exec sleep infinity")
            root_pane = tmux(
                "display-message", "-p", "-t", f"{config.session}:{window}",
                "#{pane_id}", capture=True)
            build_node(config.session, window, root_pane, node)
            apply_saved_layout(config, window, node)
        configure_session(config)
        tmux("select-window", "-t", f"{config.session}:{next(iter(config.windows))}")
    except Exception:
        if created and session_exists(config.session):
            tmux("kill-session", "-t", config.session, check=False)
        raise


def stop_dashboard(config: DashboardConfig) -> None:
    if session_exists(config.session):
        tmux("kill-session", "-t", config.session)


def save_layouts(config: DashboardConfig, only_window: str | None = None) -> None:
    require_tmux()
    if not session_exists(config.session):
        raise RuntimeError(f"tmux 会话 {config.session!r} 尚未运行")
    if only_window and only_window not in config.windows:
        raise DashboardConfigError(f"配置中没有窗口 {only_window!r}")
    selected = [only_window] if only_window else list(config.windows)
    state_path = resolve_state_path()
    state = load_layout_state(state_path)
    for window in selected:
        node = config.windows[window]
        layout = tmux(
            "display-message", "-p", "-t", f"{config.session}:{window}",
            "#{window_layout}", capture=True)
        state[window] = {
            "signature": layout_signature(node),
            "panels": panel_ids(node),
            "layout": layout,
        }
    save_layout_state(state_path, state)
    print(f"已保存窗口布局：{', '.join(selected)}")


def attach_dashboard(config: DashboardConfig) -> None:
    start_dashboard(config)
    if os.environ.get("TMUX"):
        os.execvp("tmux", ["tmux", "switch-client", "-t", config.session])
    os.execvp("tmux", ["tmux", "attach-session", "-t", config.session])


def open_dashboard(config: DashboardConfig) -> None:
    start_dashboard(config)
    if os.environ.get("TMUX"):
        os.execvp("tmux", ["tmux", "switch-client", "-t", config.session])
    if shutil.which("gnome-terminal"):
        os.execvp("gnome-terminal", [
            "gnome-terminal", "--maximize", "--title=IIS Operations Dashboard",
            "--", str(ENTRYPOINT), "attach"])
    if shutil.which("x-terminal-emulator"):
        os.execvp("x-terminal-emulator", [
            "x-terminal-emulator", "-T", "IIS Operations Dashboard",
            "-e", str(ENTRYPOINT), "attach"])
    os.execvp("tmux", ["tmux", "attach-session", "-t", config.session])


def list_panels() -> None:
    seen: set[str] = set()
    for path in sorted(PANELS_DIR.iterdir()):
        if not path.is_file() or path.name.startswith("_"):
            continue
        panel_id = path.stem if path.suffix in {".sh", ".py"} else path.name
        if panel_id not in seen:
            print(panel_id)
            seen.add(panel_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IIS Linux tmux 运维仪表盘")
    parser.add_argument("--config", help="覆盖默认 dashboard.yaml 路径")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("start", "attach", "open", "restart", "reload", "stop",
                   "status", "validate", "list-panels"):
        subparsers.add_parser(action)
    save_parser = subparsers.add_parser("save-layout")
    save_parser.add_argument("--window", help="只保存指定窗口；默认保存全部")
    return parser


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(effective_argv or ["open"])
    try:
        if args.action == "list-panels":
            list_panels()
            return 0
        config = load_config(resolve_config_path(args.config))
        if args.action == "validate":
            print(f"配置有效：{config.path}（{len(config.windows)} 个窗口）")
        elif args.action == "start":
            start_dashboard(config)
        elif args.action == "attach":
            attach_dashboard(config)
        elif args.action == "open":
            open_dashboard(config)
        elif args.action in {"restart", "reload"}:
            require_tmux()
            stop_dashboard(config)
            start_dashboard(config)
        elif args.action == "stop":
            require_tmux()
            stop_dashboard(config)
        elif args.action == "status":
            require_tmux()
            if not session_exists(config.session):
                print(f"IIS dashboard session {config.session!r} is stopped.")
                return 1
            print(f"IIS dashboard session {config.session!r} is running.")
            print(tmux("list-windows", "-t", config.session, capture=True))
        elif args.action == "save-layout":
            save_layouts(config, args.window)
        return 0
    except (DashboardConfigError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"dashboard: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
